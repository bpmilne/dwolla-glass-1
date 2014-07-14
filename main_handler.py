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

"""Request Handler for /main endpoint."""




import io
import jinja2
import logging
import os
import webapp2
import urllib
from google.appengine.api import memcache
from google.appengine.api import urlfetch
from google.appengine.ext import db

import _keys
from dwolla import DwollaClientApp,DwollaUser
Dwolla = DwollaClientApp(_keys.apiKey, _keys.apiSecret)

import httplib2
from apiclient import errors
from apiclient.http import MediaIoBaseUpload
from apiclient.http import BatchHttpRequest
from oauth2client.appengine import StorageByKeyName

from model import Credentials
from model import DwollaCredentials
import util

PASSWORD = "agatw52fs"

PAGINATED_HTML = """
<article class='auto-paginate'>
<h2 class='blue text-large'></h2>
<p></p>
</article>
"""

jinja_environment = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__)))

class _BatchCallback(object):
  """Class used to track batch request responses."""

  def __init__(self):
    """Initialize a new _BatchCallback object."""
    self.success = 0
    self.failure = 0

  def callback(self, request_id, response, exception):
    """Method called on each HTTP Response from a batch request.
      https://developers.google.com/api-client-library/python/guide/batch
    """
    if exception is None:
      self.success += 1
    else:
      self.failure += 1
      logging.error(
          'Failed to insert item for user %s: %s', request_id, exception)

class DwollaPinHandler(webapp2.RequestHandler):

  def _render_template(self, message=None):
    """Render the main page template."""
    template_values = {'userId': self.userid}
    if message:
      template_values['message'] = message

    dwolla = db.Query(DwollaCredentials).filter('userid =', self.userid).get()

    if dwolla != None:
      template_values['dwollaIsAuthed'] = True

      if dwolla.pin != None:
          template_values['note'] = 'some information about changing pins'
          template_values['pinIsEntered'] = True
      
      else: 
          template_values['note'] = 'please enter a pin'

    template = jinja_environment.get_template('templates/dwolla/changepin_index.html')
    self.response.out.write(template.render(template_values))

  @util.auth_required
  def get(self):  
    message = 'hello'
    self._render_template(message)
  
  @util.auth_required
  def post(self):  
      dwolla = db.Query(DwollaCredentials).filter('userid =', self.userid).get()
      newpin = self.request.get('newPIN')
      if len(newpin) == 4 and newpin.isdigit():
        dwolla.pin = util.encrypt(newpin, PASSWORD)
        dwolla.put()
        self.redirect('/')
        #decryption logic - self.response.out.write(util.decrypt(dwolla.pin, PASSWORD))
      else:
        self.response.out.write('invalid pin')

class DwollaOauthHandler(webapp2.RequestHandler):
  def get(self):
    oauth_return_url =  'http://' + self.request.host + '/dwolla/return'
    permissions = 'Send|Transactions|Balance|Request|Contacts|AccountInfoFull'
    authUrl = Dwolla.init_oauth_url(str(oauth_return_url), permissions)
    self.redirect(authUrl)

class DwollaOauthReturnHandler(webapp2.RequestHandler):
  @util.auth_required
  def get(self):
    Dwolla = DwollaClientApp(_keys.apiKey, _keys.apiSecret)
    oauth_return_url =  'http://' + self.request.host + '/dwolla/return'
    code = self.request.get("code")
    dwolla_token = Dwolla.get_oauth_token(code, redirect_uri=oauth_return_url)
    userid = self.userid

    dwolla = DwollaUser(dwolla_token).get_account_info()

    # Store the credentials in the data store using the userid as the key.
    creds = DwollaCredentials(userid = userid,
                              token = dwolla_token,
                              dwollaId = dwolla['Id']).put()
    logging.info('Successfully stored credentials for user: %s', userid)
    
    self.redirect_to('dwolla_pin')
    #self.response.out.write('Your never-expiring OAuth access token is: <b>%s</b>' % dwolla_token)
class MainHandler(webapp2.RequestHandler):
  """Request Handler for the main endpoint."""

  def _render_template(self, message=None):
    """Render the main page template."""
    template_values = {'userId': self.userid}
    if message:
      template_values['message'] = message

    subscriptions = self.mirror_service.subscriptions().list().execute()
    for subscription in subscriptions.get('items', []):
      collection = subscription.get('collection')
      if collection == 'timeline':
        template_values['timelineSubscriptionExists'] = True
      elif collection == 'locations':
        template_values['locationSubscriptionExists'] = True
    
    dwolla = db.Query(DwollaCredentials).filter('userid =', self.userid).get()
    pin = dwolla.pin

    if pin == None:
      template_values['note'] = 'please enter a pin'

    if pin != None:
        template_values['note'] = 'some information about changing pins'
        template_values['pinIsEntered'] = True

    if dwolla != None:
        template_values['dwollaIsAuthed'] = True

    template = jinja_environment.get_template('templates/index.html')
    self.response.out.write(template.render(template_values))

  @util.auth_required
  def get(self):
    """Render the main page."""
    # Get the flash message and delete it.
    message = memcache.get(key=self.userid)
    memcache.delete(key=self.userid)
    self._render_template(message)

  @util.auth_required
  def post(self):
    """Execute the request and render the template."""
    operation = self.request.get('operation')
    # Dict of operations to easily map keys to methods.
    operations = {
        'insertSubscription': self._insert_subscription,
        'deleteSubscription': self._delete_subscription,
        'insertItem': self._insert_item,
        'insertPaginatedItem': self._insert_paginated_item,
        'insertItemWithAction': self._insert_item_with_action,
        'insertItemAllUsers': self._insert_item_all_users,
        'insertContact': self._insert_contact,
        'deleteContact': self._delete_contact,
        'deleteTimelineItem': self._delete_timeline_item
    }
    if operation in operations:
      message = operations[operation]()
    else:
      message = "I don't know how to " + operation
    # Store the flash message for 5 seconds.
    memcache.set(key=self.userid, value=message, time=5)
    self.redirect('/')

  def _insert_subscription(self):
    """Subscribe the app."""
    # self.userid is initialized in util.auth_required.
    body = {
        'collection': self.request.get('collection', 'timeline'),
        'userToken': self.userid,
        'callbackUrl': util.get_full_url(self, '/notify')
    }
    # self.mirror_service is initialized in util.auth_required.
    self.mirror_service.subscriptions().insert(body=body).execute()
    return 'Application is now subscribed to updates.'

  def _delete_subscription(self):
    """Unsubscribe from notifications."""
    collection = self.request.get('subscriptionId')
    self.mirror_service.subscriptions().delete(id=collection).execute()
    return 'Application has been unsubscribed.'

  def _insert_item(self):
    """Insert a timeline item."""
    logging.info('Inserting timeline item')
    body = {
        'notification': {'level': 'DEFAULT'}
    }
    if self.request.get('html') == 'on':
      body['html'] = [self.request.get('message')]
    else:
      body['text'] = self.request.get('message')

    media_link = self.request.get('imageUrl')
    if media_link:
      if media_link.startswith('/'):
        media_link = util.get_full_url(self, media_link)
      resp = urlfetch.fetch(media_link, deadline=20)
      media = MediaIoBaseUpload(
          io.BytesIO(resp.content), mimetype='image/jpeg', resumable=True)
    else:
      media = None

    # self.mirror_service is initialized in util.auth_required.
    self.mirror_service.timeline().insert(body=body, media_body=media).execute()
    return  'A timeline item has been inserted.'

  def _insert_paginated_item(self):
    """Insert a paginated timeline item."""
    logging.info('Inserting paginated timeline item')
    body = {
        'html': PAGINATED_HTML,
        'notification': {'level': 'DEFAULT'},
        'menuItems': [{
            'action': 'OPEN_URI',
            'payload': 'https://www.google.com/search?q=cat+maintenance+tips'
        }]
    }
    # self.mirror_service is initialized in util.auth_required.
    self.mirror_service.timeline().insert(body=body).execute()
    return  'A timeline item has been inserted.'

  def _insert_item_with_action(self):
    """Insert a timeline item user can reply to."""
    logging.info('Inserting timeline item')
    body = {
        'creator': {
            'displayName': 'Python Starter Project',
            'id': 'PYTHON_STARTER_PROJECT'
        },
        'text': 'Tell me what you had for lunch :)',
        'notification': {'level': 'DEFAULT'},
        'menuItems': [{'action': 'REPLY'}]
    }
    # self.mirror_service is initialized in util.auth_required.
    self.mirror_service.timeline().insert(body=body).execute()
    return 'A timeline item with action has been inserted.'

  def _insert_item_all_users(self):
    """Insert a timeline item to all authorized users."""
    logging.info('Inserting timeline item to all users')
    users = Credentials.all()
    total_users = users.count()

    if total_users > 10:
      return 'Total user count is %d. Aborting broadcast to save your quota' % (
          total_users)
    body = {
        'text': 'Hello Everyone!',
        'notification': {'level': 'DEFAULT'}
    }

    batch_responses = _BatchCallback()
    batch = BatchHttpRequest(callback=batch_responses.callback)
    for user in users:
      creds = StorageByKeyName(
          Credentials, user.key().name(), 'credentials').get()
      mirror_service = util.create_service('mirror', 'v1', creds)
      batch.add(
          mirror_service.timeline().insert(body=body),
          request_id=user.key().name())

    batch.execute(httplib2.Http())
    return 'Successfully sent cards to %d users (%d failed).' % (
        batch_responses.success, batch_responses.failure)

  def _insert_contact(self):
    """Insert a new Contact."""
    logging.info('Inserting contact')
    id = self.request.get('id')
    name = self.request.get('name')
    image_url = self.request.get('imageUrl')
    if not name or not image_url:
      return 'Must specify imageUrl and name to insert contact'
    else:
      if image_url.startswith('/'):
        image_url = util.get_full_url(self, image_url)
      body = {
          'id': id,
          'displayName': name,
          'imageUrls': [image_url],
          'acceptCommands': [{ 'type': 'TAKE_A_NOTE' }]
      }
      # self.mirror_service is initialized in util.auth_required.
      self.mirror_service.contacts().insert(body=body).execute()
      return 'Inserted contact: ' + name

  def _delete_contact(self):
    """Delete a Contact."""
    # self.mirror_service is initialized in util.auth_required.
    self.mirror_service.contacts().delete(
        id=self.request.get('id')).execute()
    return 'Contact has been deleted.'

  def _delete_timeline_item(self):
    """Delete a Timeline Item."""
    logging.info('Deleting timeline item')
    # self.mirror_service is initialized in util.auth_required.
    self.mirror_service.timeline().delete(id=self.request.get('itemId')).execute()
    return 'A timeline item has been deleted.'


MAIN_ROUTES = [
    webapp2.Route('/', handler=MainHandler, name='home'),
    webapp2.Route('/dwolla', handler=DwollaOauthHandler, name='dwolla'),
    webapp2.Route('/dwolla/return', handler=DwollaOauthReturnHandler, name='dwolla_oauth_return'),
    webapp2.Route('/dwolla/pin', handler=DwollaPinHandler, name='dwolla_pin'),
]

