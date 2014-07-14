# Copyright (C) 2014 The Members Group
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Request Handler for /notify endpoint."""

import re
import io
import json
import logging
import webapp2
import _keys

from time_convert import parse_date
from datetime import datetime
from datetime import timedelta
from random import choice
from apiclient.http import MediaIoBaseUpload
from oauth2client.appengine import StorageByKeyName
from google.appengine.ext import db
from dwolla import DwollaClientApp, DwollaUser, DwollaAPIError
from model import Credentials, DwollaCredentials
import util

PASSWORD = ""


class NotifyHandler(webapp2.RequestHandler):
  """Request Handler for notification pings."""

  def post(self):
    """Handles notification pings."""
    logging.info('Got a notification with payload %s', self.request.body)
    data = json.loads(self.request.body)
    userid = data['userToken']
    # TODO: Check that the userToken is a valid userToken.

    self.mirror_service = util.create_service(
        'mirror', 'v1',
        StorageByKeyName(Credentials, userid, 'credentials').get())
    if data.get('collection') == 'locations':
      self._handle_locations_notification(data)
    elif data.get('collection') == 'timeline':
      self._handle_timeline_notification(data)

  def _handle_locations_notification(self, data):
    """Handle locations notification."""
    Dwolla = DwollaClientApp(_keys.apiKey, _keys.apiSecret)

    location = self.mirror_service.locations().get(id=data['itemId']).execute()

    loc = 'dg-' + str(round(location.get('latitude'), 3)) + 'x' + str(round(location.get('longitude'), 3))
    logging.info('Your loc is ' + loc)

    items = self.mirror_service.timeline().list().execute()

    loc_exists = False

    for i in items['items']:
    #  FIXME: need to not send down items every 10 minutes 
    #   if i['bundleId'] == loc:
    #     loc_exists = True

    #   logging.info('parsed date')
    #   try:
    #     logging.info(parse_date(i['created']))
    #   except: 
    #     pass

    #   logging.info(type(i['created']))
    #   logging.info('current time plus 5:')
    #   logging.info(datetime.now() + timedelta(minutes=5))

    #   if i['created'] < (datetime.now() + timedelta(minutes=5)):
    #     loc_exists = False


    # if loc_exists == False:
      spots = Dwolla.get_nearby_spots()#lat=location.get('latitude'), lon=location.get('longitude'))
      for spot in spots:
        html = """<article><section class="align-center text-auto-size"><p>%s is nearby.</p></section><footer class="align-center">eyepay</footer></article>""" % (spot['Name'])
        logging.info('Inserting timeline item')
        
        body = {
            "html": html,
            "bundleId": loc,
            "menuItems": [],
            "notification": {
              "level": "DEFAULT"
            }
          }

        amt = 0
        while amt < 1:
          amt += 0.01
          id_text = str(amt) + "@" + spot['Id']
          body['menuItems'].append({
                                  "action": "CUSTOM",
                                  "id": id_text,
                                  "values": [{
                                    "displayName": "Pay $%s" % (amt),
                                    "iconUrl": "http://upload.wikimedia.org/wikipedia/commons/e/ec/Blank_50px.png"
                                  }]
                                })

        self.mirror_service.timeline().insert(body=body).execute()

  def _handle_timeline_notification(self, data):
    """Handle timeline notification."""
    for user_action in data.get('userActions', []):
      # Fetch the timeline item.
      item = self.mirror_service.timeline().get(id=data['itemId']).execute()
      item_id = data['itemId']
      
      old_item = item
      logging.info(user_action.get('payload'))
      userid = data['userToken']
      dwollaCreds = db.Query(DwollaCredentials).filter('userid =', data['userToken']).get()

      if user_action.get('type') == 'CUSTOM':
        split = re.split(r"@", user_action.get('payload'))
        amount = split[0]
        destination = split[1]
        pin = util.decrypt(dwollaCreds.pin, PASSWORD)
    
        try: 
          transaction_id = DwollaUser(dwollaCreds.token).send_funds(amount, destination, pin)
          transaction = DwollaUser(dwollaCreds.token).get_transaction(transaction_id)
          logging.info("transaction info")
          logging.info(transaction)
          item['html'] = """<article> <section> Successfully paid $%s to %s! </section> </article>""" % (transaction['Amount'], transaction['DestinationName'])
          
        except DwollaAPIError, e: 
          item['html'] = """<article> <section> An error occured: %s </section> </article>""" % (e)
        
        #find all bundle items
        items = self.mirror_service.timeline().list().execute()
        items = items['items']
        #delete all except current one - need more robust error checking here
        logging.info('current item: ' + item_id)
        
        for i in items:
            if i['id'] != item_id:
              logging.info('delete ' + i['id'])   
              try:
                self.mirror_service.timeline().delete(id=i['id']).execute()
              except: 
                pass

        item['bundleId'] = 'dg-success'
        item['menuItems'] = [{ 'action': 'DELETE' }];
        
        self.mirror_service.timeline().update(id=item['id'], body=item).execute()

      else:
        logging.info(
            "I don't know what to do with this notification: %s", user_action)


NOTIFY_ROUTES = [
    ('/notify', NotifyHandler)
]
