
# $Id$

#
# Under GNU General Public License
#
# Author:   noalwin
# Email:    lambda512@gmail.com
# JabberID: lambda512@jabberes.com
#


# TODO: Multithread
# TODO: Testing
# TODO: Simplify _discover_item()


from ConfigParser import SafeConfigParser
import logging
from os.path import abspath, dirname, join
from random import choice
import re
import sys
import xml


from xmpp import Client, features, NodeProcessed
from xmpp.protocol import DataForm, Iq, isResultNode, Message, Presence


# Load the configuration
SCRIPT_DIR = abspath(dirname(sys.argv[0]))
cfg = SafeConfigParser()
cfg.readfp(open(join(SCRIPT_DIR, 'config.cfg')))

USE_MULTIPLE_QUERY_ACCOUNTS = cfg.getboolean("xmpp discoverer", "USE_MULTIPLE_QUERY_ACCOUNTS")
ONLY_USE_SUCCESFULL_CLIENT = cfg.getboolean("xmpp discoverer", "ONLY_USE_SUCCESFULL_CLIENT")
ONLY_RETRY_SERVERS = cfg.getboolean("xmpp discoverer", "ONLY_RETRY_SERVERS")
INFO_QUERY_RETRIES = cfg.getint("xmpp discoverer", "INFO_QUERY_RETRIES")
ITEM_QUERY_RETRIES = cfg.getint("xmpp discoverer", "ITEM_QUERY_RETRIES")

# Jabber account

account_number = 1
JABBER_ACCOUNTS = []
while (cfg.has_section("Jabber account %d" % account_number)):
	JABBER_ACCOUNTS.append( {
	    'user': cfg.get("Jabber account %d" % account_number, "USER"),
	    'password': cfg.get("Jabber account %d" % account_number, "PASSWORD"),
	    'resource': cfg.get("Jabber account %d" % account_number, "RESOURCE"),
	    'server': cfg.get("Jabber account %d" % account_number, "SERVER")
	} )
	account_number += 1

del(account_number)

if len(JABBER_ACCOUNTS) == 0:
	raise Exception("No jabber accounts found. Check your configuration")


# Gateway accounts

GATEWAY_ACCOUNTS = {}
for section in cfg.sections():
	# Find [category type gateway account] sections
	if section.endswith(' gateway account'):
		words = section.split()
		GATEWAY_ACCOUNTS[(words[0], words[1])] = dict(cfg.items(section))

del(words)
del(section)

del(cfg)


URLREGEXP = re.compile(
	r'(?P<fullsubdomain>' +
		r'(?:(?P<subdomain>\w+)\.(?=\w+\.\w))?' +
		r'(?P<fulldomain>'+
			r'(?:(?P<domain>\w+)\.)?(?P<tld>\w+)$' +
	   r')' +
	r')'
	)


def _in_same_domain(parent, child):
	'''Check if parent and child are in the same domain'''
	
	if child.count('@') > 0:
		return False
	
	parent_match = URLREGEXP.search(parent)
	child_match = URLREGEXP.search(child)
	
	if child_match.group('tld') == 'localhost':
		return True
	elif 'domain' not in child_match.groupdict():
		return True
	elif child_match.group('domain') in ('com', 'net', 'org',
		                                'co', 'gov', 'edu'):
		# It's a country code second level domain, until the third level
		return (parent_match.group('fullsubdomain') == 
		        child_match.group('fullsubdomain'))
	else:
		# It's a usual domain name, check until the second level
		return (parent_match.group('fulldomain') == child_match.group('fulldomain'))

MESSAGES = {}

def _handle_messages(con, message):
	'''Store received messages'''
	#message = Message(node=event)
	
	typ = message.getType()
	fromjid = message.getFrom().getStripped()
	body = message.getTagData('body')
	logging.debug('Handling %s message from %s: %s', typ, fromjid, body)
	
	if MESSAGES.has_key(fromjid):
		if MESSAGES[fromjid].has_key(typ):
			MESSAGES[fromjid][typ].append(body)
		else:
			MESSAGES[fromjid][typ] = [body]
	else:
		MESSAGES[fromjid] = {typ: [body]}
	
	raise NodeProcessed


def _get_version(component, dispatcher):
	version = {}
	node = dispatcher.SendAndWaitForResponse(Iq(to=component[u'jid'], typ='get',
	                                            queryNS='jabber:iq:version'))
	if isResultNode(node):
		for element in node.getTag('query').getChildren():
			version[element.getName()] = element.getData()
	
	return version


def _get_reg_fields(dispatcher, jid, only_required=True):
	'''Get the input fields from the registration form.
	Returns a dictionary {field:value} where value is None if the form field is empty'''
	
	reg_fields = {}
	
	reg_info = features.getRegInfo(dispatcher._owner, jid)
	
	form = reg_info.getTag('query').getTag('x', attrs={'type':'form'}, namespace='jabber:x:data')
	
	if form: # x:data form is present
		# TODO: Rewrite it using DataForm class?
		if form.getTag('registered'):
			logging.warning('Already registered in %s gateway', jid)
		
		fields = [field for field in form.getTags('field') if field.getAttr('type') != 'fixed']
		has_required_fields = False
		
		for field in fields:
			if only_required and field.getTag('required'):
				if has_required_fields == False:
					has_required_fields = True
					# Previous fields aren't required, so delete them
					reg_fields = {}
			if (not has_required_fields) or field.getTag('required'):
				if field.getTag('value'):
					reg_fields[field.getAttr('var')] = field.getTag('value').getData() or None
				else:
					reg_fields[field.getAttr('var')] = None
			else:
				logging.debug('Ignoring %s field not required in %s gateway',
				                field.getAttr('var'), jid)
	else: # iq:register fields
		reg_fields = dict(
		      [(children.getName(), children.getData()) for children in reg_info.getTag('query').getChildren() \
		          if children.getName() in (
		                'username', 'nick', 'password', 'name', 'first', 'last',
		                'email', 'address', 'city', 'state', 'zip', 'phone', 'url',
		                'date', 'misc', 'text', 'key')])
	
	return reg_fields, bool(form)


def _unregister(dispatcher, roster, jid):
	'''Unregister from gaweway. Used internally by _test_gateway()'''
	
	if features.unregister(dispatcher._owner, jid) != 1:
		logging.error('Error unregistering from %s gateway', jid)
	else:
		logging.debug('Unregistering from %s gateway', jid)
	
	roster.delItem(jid)


def _try_register(dispatcher, jid, account, use_data_form):
	'''Try to register and unregister as specified on XEP-0100'''
	
	# Perform registration
	
	if use_data_form:
		reg_iq = Iq(to=jid, typ='set', queryNS='jabber:iq:register')
		data_form = DataForm('submit', account)
		
		reg_iq.getTag('query').addChild(node=data_form)
		node = dispatcher.SendAndWaitForResponse(reg_iq)
	else:
		
		if features.register(dispatcher._owner, jid, info=account) == 1:
			# Success
			logging.debug('Registered on %s gateway', jid)
		else:
			#print dispatcher._owner.lastErrCode.encode('utf-8'), ' ', dispatcher._owner.lastErr.encode('utf-8')
			logging.debug('Can not register on %s gateway (%s: %s)', jid,
			              dispatcher._owner.lastErrCode, dispatcher._owner.lastErr)
			return False
	
	# TODO: change variable name
	cl = dispatcher._owner
	roster = cl.getRoster()
	
	# Seconds to wait
	# A openfire gtalk gateway on localhost can take 21 seconds on inform of a
	# invalid username/password
	max_wait = 30
	
	# Wait for a error message (Openfire gateways send messages)
	for time in range(0, max_wait):
		if (  MESSAGES.has_key(jid) and MESSAGES[jid].has_key('error')
		      and len(MESSAGES[jid]['error']) > 0 ) :
			for message in MESSAGES[jid]['error']:
				logging.warning('Error message received from %s gateway: %s', jid, message)
			MESSAGES[jid]['error'] = []
			_unregister(dispatcher, roster, jid)
			return False
			#break
		cl.Process(1)
	
	if time < max_wait-1:
		_unregister(dispatcher, roster, jid)
		return False
	
	
	# Perform subscriptions
	
	# Wait subscrition request
	for time in range(0, max_wait):
		if jid in roster.keys() and roster.getSubscriptionFromStatus(jid) != None:
			break
		cl.Process(1)
	
	if time >= max_wait-1:
		# The gateway didn't requested subscription
		logging.debug('Subcription request from %s gateway not received', jid)
		_unregister(dispatcher, roster, jid)
		return False
	
	if roster.getSubscriptionFromStatus(jid) == 'pending':
		roster.Authorize(jid)
	
	if roster.getSubscriptionToStatus(jid) == None:
		roster.Subscribe(jid)
	for time in range(0, max_wait):
		if roster.getSubscriptionToStatus(jid) == 'subscribed':
			break
		cl.Process(1)
	
	if time >= max_wait-1:
		# The gateway rejected our subscription request
		logging.debug('Subcription request rejected by %s gateway', jid)
		_unregister(dispatcher, roster, jid)
		return False
	
	# Try to login
	dispatcher.send(Presence(jid))
	for time in range(0,max_wait):
		if len(roster.getResources(jid)) > 0:
			break
		cl.Process(1)
	
	if time >= max_wait-1:
		# Login failed
		logging.debug('Can not login on %s gateway', jid)
		_unregister(dispatcher, roster, jid)
		return False
	
	logging.debug('Successfull login on %s gateway', jid)
	
	# Successfull registration, now unregister
	_unregister(dispatcher, roster, jid)
	return True


def _test_gateway(dispatcher, jid, service_category, service_type):
	'''Select the account data and try to register on a gateway'''
	
	# Guess if the xmpp gateway is a GoogleTalk gateway
	# If so, use Google account
	if service_category == 'gateway' and service_type == 'xmpp' and  jid.startswith('gtalk.'):
		# TODO: Check also the service Name from service discovery
		raise Exception('Not needed')
		service_type = 'gtalk'
	
	
	if (service_category, service_type) in GATEWAY_ACCOUNTS:
		account = GATEWAY_ACCOUNTS[(service_category, service_type)]
		
		required_fields, is_form = _get_reg_fields(dispatcher, jid)
		
		fields_not_available = [field for field, value in required_fields.iteritems() if field not in account and value is None]
		if fields_not_available:
			logging.error('Not enough data to test register on %s %s/%s gateway %s',
			              jid, service_category, service_type, str(fields_not_available))
			return True
		
		account = dict([(field, account[field] if field in account else value) for field, value in required_fields.iteritems()])
		
		# Openfire XMPP gateway uses username as the Jabber ID
		# J2J Transport (http://JRuDevels.org) Twisted-version uses username and server
		# Openfire doesn't check if it's a valid JID, so this is unnecesary
		# but could be neccesary in the future
		if service_category == 'gateway' and service_type == 'xmpp' and 'server' not in account:
			account['username'] = '%s@%s' % ( account['username'],
			                  GATEWAY_ACCOUNTS[('gateway', 'xmpp')]['server'] )
		
		return _try_register(dispatcher._owner, jid, account, is_form)
		
	else:
		# We can't test the gateway, assume that it works
		return True


def _add_to_services_list(services_list, service_category_type, component):
	'''Add the compoenent to the server services list.
	There can be several components providing the same service.'''
	if service_category_type in services_list:
		services_list[service_category_type].append(component)
	else:
		services_list[service_category_type] = [(component)]


def _guess_component_info(component):
	'''Guess and add the service info using the JID'''
	
	jid = component[u'jid']
	#info = ([{u'category': None, u'type': None}], [])
	info = ([], [])
	
	logging.debug('Guessing type of %s', jid)
	
	# Server
	if jid in SERVER_LIST:
		info = ( [{u'category': u'server', u'type': u'im'}], [] )
	# Conference
	elif ( jid.startswith((u'conference.', u'conf.', u'muc.', u'chat.', u'rooms.'))
	     and not ( '.yahoo.' in jid or '.irc.' in jid ) ):
		# MUC
		info = ( [ {u'category': u'conference', u'type': u'text'},
		           {u'category': u'conference', u'type': u'x-muc'} ],
		         [u'http://jabber.org/protocol/muc'] )
	elif jid.startswith(u'irc.'):
		info = ( [{u'category': u'conference', u'type': u'irc'}], [] )
	
	# Transports
	elif jid.startswith((u'aim.', u'aim-jab.')):
		info = ( [{u'category': u'gateway', u'type': u'aim'}], [] )
	elif jid.startswith(u'aim-icq.'):
		info = ( [ {u'category': u'gateway', u'type': u'aim'},
	               {u'category': u'gateway', u'type': u'icq'} ], [] )
	elif jid.startswith((u'gg.', u'gadugadu.', u'gadu-gadu.')):
		info = ( [{u'category': u'gateway', u'type': u'gadu-gadu'}], [] )
	elif jid.startswith(u'http-ws.'):
		info = ( [{u'category': u'gateway', u'type': u'http-ws'}], [] )
	elif jid.startswith((u'icq.', u'icqt.', u'jit-icq.', u'icq-jab.', u'icq2.')):
		info = ( [{u'category': u'gateway', u'type': u'icq'}], [] )
	elif jid.startswith((u'msn.', u'msnt.', u'pymsnt.')):
		info = ( [{u'category': u'gateway', u'type': u'msn'}], [] )
	elif jid.startswith(u'qq.'):
		info = ( [{u'category': u'gateway', u'type': u'qq'}], [] )
	elif jid.startswith(u'sms.'):
		info = ( [{u'category': u'gateway', u'type': u'sms'}], [] )
	elif jid.startswith(u'smtp.'):
		info = ( [{u'category': u'gateway', u'type': u'smtp'}], [] )
	elif jid.startswith(u'tlen.'):
		info = ( [{u'category': u'gateway', u'type': u'tlen'}], [] )
	elif jid.startswith(u'xfire.'):
		info = ( [{u'category': u'gateway', u'type': u'xfire'}], [] )
	elif jid.startswith((u'xmpp.', u'j2j.')):
		info = ( [{u'category': u'gateway', u'type': u'xmpp'}], [] )
	elif jid.startswith(u'yahoo.'):
		info = ( [{u'category': u'gateway', u'type': u'yahoo'}], [] )
	
	# Directories
	elif jid.startswith((u'jud.', u'vjud.', u'search.', u'users.')):
		info = ( [{u'category': u'directory', u'type': u'user'}], [] )
	
	# PubSub
	elif jid.startswith(u'pubsub.'):
		info = ( [{u'category': u'pubsub', u'type': u'service'}], [] )
	elif jid.startswith(u'pep.'):
		info = ( [{u'category': u'pubsub', u'type': u'pep'}], [] )
	
	# Presence
	elif jid.startswith((u'presence.', u'webpresence.', u'status.')):
		info = ( [{u'category': u'component', u'type': u'presence'}], [] )
	
	# Headline
	elif jid.startswith((u'newmail.', u'mail.', u'jmc.')):
		info = ( [{u'category': u'headline', u'type': u'newmail'}], [] )
	elif jid.startswith(u'rss.'):
		info = ( [{u'category': u'headline', u'type': u'rss'}], [] )
	elif jid.startswith(u'weather.'):
		info = ( [{u'category': u'headline', u'type': u'weather'}], [] )
	
	# Proxy
	elif jid.startswith((u'proxy.', u'proxy65.')):
		info = ( [{u'category': u'proxy', u'type': u'bytestreams'}], [] )
		
	# Store
	elif jid.startswith((u'file.', u'disk.', u'jdisk.', u'dysk.')):
		info = ( [{u'category': u'store', u'type': u'file'}], [] )
	
	
	# Non standard
	elif jid.startswith(u'gtalk.'):
		info = ( [{u'category': u'gateway', u'type': u'gtalk'}], [] )
	
	
	return info


def _normalize_identities(component):
	
	for identity in component[u'info'][0]:
		
		# MUC is not the only service that announces conference:text and
		# some IRC transports even annountce the feature
		# 'http://jabber.org/protocol/muc', so try to detect the pure MUC
		# services and add them also in a special category:type conference:x-muc
		
		if identity[u'category']=='conference' and identity[u'type']=='text':
			if (  not ( ('name' in component and 'IRC' in component['name']) or
			            '.irc.' in component['jid'] or
			            component['jid'].startswith('irc.') ) and
			      u'http://jabber.org/protocol/muc' in component[u'info'][1]):
				#_add_to_services_list( services_list, ('conference', 'x-muc'), component )
				# Add fake identity
				component[u'info'][0].append({u'category': u'conference', u'type': 'x-muc'})
			#continue
		
		# Change gateway/xmpp identities on gtalk transports to gateway/gtalk
		if identity['category'] == 'gateway' and identity['type'] == 'xmpp' and (
		        component[u'jid'].startswith('gtalk.') or (identity.has_key('name') and (
		                identity['name'].lower().find('google') != -1 or
		                identity['name'].lower().find('gtalk') != -1 ))):
			# If is a gtalk transport
			has_gtalk_identity = False
			for iden in component[u'info'][0]:
				if iden['category'] == 'gateway' and iden['type'] == 'gtalk':
					has_gtalk_identity = True
					break
			if not has_gtalk_identity:
				# And gateway/gtalk is not already in the identities
				identity[u'type'] = 'gtalk'
		
		# Adapt non standard indentities to standard equivalents
		
		# Openfire has tho components for the irc gateway
		# - irc.server category:gateway type:irc with jabber:iq:gateway feature
		# - conference.irc.server category:conference type:text without jabber:iq:gateway feature but with http://jabber.org/protocol/muc feature
		# Add both components as irc gateway, though it's not a very good solution
		if identity[u'category']=='gateway' and identity[u'type']=='irc':
			identity[u'category'] = 'conference'
		if (identity[u'category']=='conference' and identity[u'type']=='text' and
		    '.irc.' in component[u'jid']):
			identity[u'type'] = 'irc'
		
		# ejabberd1.1.3 uses pubsub:generic instead pubsub:service
		if identity[u'category']=='pubsub' and identity[u'type']=='generic':
			identity[u'type'] = 'service'
		
		# ejabberd's webpresence module uses presence:text instead component:presence
		if identity[u'category']=='presence' and identity[u'type']=='text':
			identity[u'category'] = 'component'
			identity[u'type'] = 'presence'
		
		# Some weather components use agent:weather instead headline:weather
		if identity[u'category']=='agent' and identity[u'type']=='weather':
			identity[u'category'] = 'headline'
		
		# PyRSS
		if identity[u'category']=='x-service' and identity[u'type']=='x-rss':
			identity[u'category'] = 'headline'
			identity[u'type'] = 'rss'
		
		#
		if identity[u'category']=='gateway' and identity[u'type']=='gadugadu':
			identity[u'category'] = 'gateway'
			identity[u'type'] = 'gadu-gadu'
		
		#
		if identity[u'category']=='gateway' and identity[u'type']=='x-tlen':
			identity[u'category'] = 'gateway'
			identity[u'type'] = 'tlen'
		
		
		# Normalize non standard indentities
		
		#
		if identity[u'category']=='gateway' and identity[u'type']=='XMPP':
			identity[u'type'] = 'xmpp'
		
		#
		if identity[u'category']=='gateway' and identity[u'type']=='gmail':
			identity[u'type'] = 'gtalk'


def _is_gateway(component):
	
	if 'jabber:iq:gateway' in component[u'info'][1]:
		return True
	
	for identity in component[u'info'][0]:
		if identity['category'] == 'gateway':
			return True
	
	return False


def _handle_component_available(component, server, dispatcher):
	
	_normalize_identities(component)
	
	available = True
	
	# If it's a gateway, be sure that we can register on it, if not, treat it as a unavailable component
	# The Openfire check is a temporary workarround
	# http://www.igniterealtime.org/community/thread/34023
	# http://www.igniterealtime.org/issues/browse/GATE-432
	
	if _is_gateway(component):
		if 'jabber:iq:register' not in component[u'info'][1]:
			available = False
		elif 'jabber:iq:version' in component[u'info'][1]:
			if _get_version(component, dispatcher)['name'].startswith('Openfire '):
				available = False
			else:
				for identity in component[u'info'][0]:
					if _test_gateway(dispatcher, component[u'jid'], identity[u'category'], identity[u'type']) == False:
						available = False
	elif component[u'jid'].startswith('conference.irc.'):
		# It's likely to be part of the old Openfire IRC Gateway.
		# Their transport was separated in two components ( irc.server and conference.irc.server)
		# Their gateways were blocked for external users.
		available = False
		
	if available:
		component['available'] = True
		#Add the component
		for identity in component[u'info'][0]:
			_add_to_services_list(server[u'available_services'], (identity[u'category'], identity[u'type']), component)
	else:
		_handle_component_unavailable(component, server)


def _handle_component_unavailable(component, server):
	
	component['available'] = False
	
	if component[u'info']==([], []):
		component[u'info'] = _guess_component_info(component)
	
	for identity in component[u'info'][0]:
		
		# TODO: some way to diference services from non-muc services
		_add_to_services_list(server[u'unavailable_services'], (identity[u'category'], identity[u'type']), component)


def _get_item_info(dispatcher, component, retries=0):
	'''Query the information about the item'''
	
	# Some components adresses ends in .localhost so the querys
	# will end on a 404 error
	# Then, we don't need to waste resources querying them
	
	if not component[u'jid'].endswith('.localhost'):
		retry = retries
		while retry >= 0:
			try:
				if u'node' in component:
					# TODO: Don't use _owner to get the client
					logging.debug( 'Trying to discover component %s (node %s) using %s@%s/%s: %d/%d retries left',
					               component[u'jid'], component[u'node'],
					               dispatcher._owner.User, dispatcher._owner.Server,
					               dispatcher._owner.Resource, retry, retries)
					info = features.discoverInfo( dispatcher, component[u'jid'],
					                              component[u'node'])
				else:
					# TODO: Don't use _owner to get the client
					logging.debug( 'Trying to discover component %s using %s@%s/%s: %d/%d retries left',
					               component[u'jid'], dispatcher._owner.User,
					               dispatcher._owner.Server, dispatcher._owner.Resource,
					               retry, retries)
					info = features.discoverInfo(dispatcher, component[u'jid'])
			except xml.parsers.expat.ExpatError:
				logging.warning( '%s sent malformed XMPP', component[u'jid'],
				                 exc_info=True)
				#return ([], [])
				#add_component_unavailable( component[u'jid'],
				                            #server[u'unavailable_services'] )
				raise
			
			if len(info[0]) != 0 or len(info[1]) != 0:
				return info
			
			retry -= 1
		
		else:
			logging.debug( 'Discarding query to component %s: Not accesible',
			               component[u'jid'] )
			return info
		
	else:
		logging.debug('Ignoring %s', component[u'jid'])
		return ([], [])


def _get_items(dispatcher, component, retries=0):
	'''Query the child items and nodes of component.
	Only returns items whose address it's equal or a subdomain of component'''
	
	retry = retries
	while retry >= 0:
		try:
			if u'node' in component:
				# TODO: Don't use _owner to get the client
				logging.debug( 'Trying to discover components of %s (node %s) using %s@%s/%s: %d/%d retries left',
				               component[u'jid'], component[u'node'],
				               dispatcher._owner.User, dispatcher._owner.Server,
				               dispatcher._owner.Resource, retry, retries)
				items = features.discoverItems( dispatcher, component[u'jid'],
				                                component[u'node'] )
			else:
				# TODO: Don't use _owner to get the client
				logging.debug( 'Trying to discover components of %s using %s@%s/%s: %d/%d retries left',
				               component[u'jid'], dispatcher._owner.User,
				               dispatcher._owner.Server, dispatcher._owner.Resource,
				               retry, retries)
				items = features.discoverItems(dispatcher, component[u'jid'])
		except xml.parsers.expat.ExpatError:
			logging.warning( '%s sent malformed XMPP', component[u'jid'],
			                 exc_info=True)
			#items = []
			raise
			
		if len(items) > 0:
			# Process items
			for item in list(items):
				# Remove items from other servers
				if not _in_same_domain(component[u'jid'], item[u'jid']):
					items.remove(item)
				
				# Remove itself if the server includes itself in the items list
				if component[u'jid'] == item[u'jid']:
					if 'node' in component and 'node' in item:
						if component[u'node'] == item[u'node']:
							items.remove(item)
					elif 'node' not in component and 'node' not in item:
						items.remove(item)
			return items
		
		retry -= 1
		
	else:
		logging.debug('Discarding query to get components of %s: Not accesible', component[u'jid'])
		return []


def _discover_item(dispatchers, component, server):
	'''Explore the component and its childs and 
	update the component list in server.
	Both, component and server, variables are modified.'''
	
	needs_to_query_items = False
	#cl.Process(1)
	
	#Only retry for servers (to avoid wasting time)
	if ONLY_RETRY_SERVERS:
		if component[u'jid'] == server[u'jid']:
			retries = INFO_QUERY_RETRIES
			item_retries = ITEM_QUERY_RETRIES
		else:
			retries = 0
			item_retries = 0
	else:
		retries = INFO_QUERY_RETRIES
		item_retries = ITEM_QUERY_RETRIES
	
	for dispatcher in dispatchers:
		try:
			component[u'info'] = _get_item_info(dispatcher, component, retries)
		except xml.parsers.expat.ExpatError:
			component[u'info'] = ([], [])
			_handle_component_unavailable(component, server)
			raise
		
		if len(component[u'info'][0]) > 0 and len(component[u'info'][1]) > 0:
			# Successfull discovery
			
			if ONLY_USE_SUCCESFULL_CLIENT:
				dispatchers = [dispatcher]
			break
	
	# Detect if it's a server or a branch (if it have child items)
	
	if (  (u'http://jabber.org/protocol/disco#info' in component[u'info'][1]) |
	      (u'http://jabber.org/protocol/disco' in component[u'info'][1])  ):
		needs_to_query_items = False
		_handle_component_available(component, server, dispatcher)
		for identity in component[u'info'][0]:
			if ( (identity['category'] == u'server') | (
			        (identity['category'] == u'hierarchy') &
			        (identity['type'] == u'branch')
			   ) ):
				needs_to_query_items = True
	
	elif u'jabber:iq:agents' in component[u'info'][1]:
		#Fake identities. But we aren't really sure that it's a server?
		component[u'info'] = ( [{u'category': u'server', u'type': u'im'}],
		                     component[u'info'][1] )
		_handle_component_available(component, server, dispatcher)
		needs_to_query_items = True
	
	elif u'jabber:iq:browse' in component[u'info'][1]: #Not sure if it's really used
		# Adapt the information
		# Process items
		component[u'items'] = []
		for item in component[u'info'][0]:
			if _in_same_domain(component[u'jid'], item[u'jid']):
				component[u'items'].append(_discover_item(dispatchers, item, server))
		
		needs_to_query_items = False # We already have the items
		#Fake identities. But we aren't really sure that it's a server?
		component[u'info'] = ( [{u'category': u'server', u'type': u'im'}],
		                       component[u'info'][1] )
		_handle_component_available(component, server, dispatcher)
	
	elif (component[u'info'] == ([], [])):
		# We have to guess what feature is using the JID
		_handle_component_unavailable(component, server)
	
	else:
		if u'available_services' in component:
			# It's a server. It probably uses jabber:iq:browse
			# Adapt the information
			# Process items
			component[u'items'] = []
			for item in component[u'info'][0]:
				if _in_same_domain(component[u'jid'], item[u'jid']):
					component[u'items'].append(_discover_item(dispatchers, item,
					                                          server))
			
			needs_to_query_items = False # We already have the items
			#Fake identities. But we aren't really sure that it's a server?
			component[u'info'] = ( [{u'category': u'server', u'type': u'im'}],
			                       component[u'info'][1] )
			_handle_component_available(component, server, dispatcher)
		else:
			#try:
			_handle_component_available(component, server, dispatcher)
			#except:
				#_handle_component_unavailable(component, server)
	
	# If it's a server or a branch node, get the child items
	
	if needs_to_query_items:
		for dispatcher in dispatchers:
			try:
				component[u'items'] = _get_items(dispatcher, component, item_retries)
			except xml.parsers.expat.ExpatError:
				component[u'items'] = []
				raise
			
			if len(component[u'items']) > 0:
				# Successfull discovery
				if ONLY_USE_SUCCESFULL_CLIENT:
					dispatchers = [dispatcher] # Uneeded filtering
				break
		
		for item in list(component[u'items']):
			if (component[u'jid'] != item[u'jid']):
				item = _discover_item(dispatchers, item, server)
			elif u'node' in component and u'node' in item:
				if (  (component[u'jid'] == item[u'jid']) &
					  (component[u'node'] != item[u'node'])  ):
					item = _discover_item(dispatchers, item, server)
			else:
				item = _discover_item(dispatchers, item, server)
	
	return component


def _show_node(node, indent=0):
	'''Print the node and its childs'''
	print node
	print ' '*indent,
	print 'JID:     ' + node[u'jid']
	
	print ' '*indent,
	print 'node:     ' + node[u'node']
	
	if u'info' in node:
		for identity in node[u'info'][0]:
			print ' '*indent,
			print 'INFO: id: ' + str(identity)
		for feature in node[u'info'][1]:
			print ' '*indent,
			print 'INFO: ft: ' + str(feature)
	
	if u'items' in node:
		for item in node[u'items']:
			_show_node(item, indent+4)
	

def _get_clients(accounts):
	'''Connect clients to the jabber accounts'''
	
	clients = []
	
	for account in accounts:
		client = Client(account['server'], debug=[])
		if not client.connect(secure=0):
			logging.error("Can not connect to %s server", account['server'])
			#raise IOError('Can not connect to server.')
			continue
		if not client.auth(account['user'], account['password'], account['resource']):
			logging.error("Can not auth as %s@%s", account['user'], account['server'])
			#raise IOError('Can not auth with server.')
			continue
		
		client.RegisterHandler('message', _handle_messages)
		client.sendInitPresence()
		client.Process(1)
		
		clients.append(client)
	
	if len(clients) == 0:
		logging.critical("Can not login into any jabber account, please check your configuration")
		raise Exception('No jabber accounts available')
	
	return clients

def _get_dispatchers(clients):
	'''Return a dispatcher list'''
	dispatchers = []
	for client in clients:
		dispatchers.append(client.Dispatcher)
	
	return dispatchers

def _keep_alive_clients(clients):
	'''Prevent client disconnections. Not sure if it really does something.'''
	for client in clients:
		client.Process(0.1)


def _disconnect_clients(clients):
	'''Disconnect clients'''
	for client in clients:
		try:
			client.Process(10)
			client.disconnect()
		except:
			# Ignore errors (i.e. Disconnections when a client has received
			# invalid stanzas)
			pass

SERVER_LIST = None
def discover_servers(server_list):
	
	global SERVER_LIST
	SERVER_LIST = server_list
	
	if USE_MULTIPLE_QUERY_ACCOUNTS:
		accounts = JABBER_ACCOUNTS
	else:
		accounts = [choice(JABBER_ACCOUNTS)]
	
	servers = {}
	
	for jid in server_list:
		servers[jid] = { u'jid': jid, u'available_services': {}, 
		                 u'unavailable_services': {} }
	
	# Connect to server
		
	#cl = Client(jabber_server, debug=[])
	#if not cl.connect(secure=0):
		#raise IOError('Can not connect to server.')
	#if not cl.auth(jabber_user, jabber_password, jabber_resource):
		#raise IOError('Can not auth with server.')
	
	#cl.sendInitPresence()
	#cl.Process(1)
	
	clients = _get_clients(accounts)
	dispatchers = _get_dispatchers(clients)
	
	logging.info('Begin discovery')
	
	try:
		for jid in sorted(servers.keys()):
			server = servers[jid]
			_keep_alive_clients(clients)
			
			try:
				_discover_item(dispatchers, server, server)
			
			except xml.parsers.expat.ExpatError: # Restart the clients
				#cl.disconnect()
				logging.warning( 'Aborting discovery of %s server. Restart clients and continue',
				                 server[u'jid'], exc_info=sys.exc_info() )
				
				_disconnect_clients(clients)
				clients = _get_clients(accounts)
				dispatchers = _get_dispatchers(clients)
				
				#cl = Client(jabber_server, debug=[])
				#if not cl.connect(secure=0):
					#raise IOError('Can not connect to server.')
				#if not cl.auth(jabber_user, jabber_password, jabber_resource):
					#raise IOError('Can not auth with server.')
				#cl.sendInitPresence()
				#cl.Process(1)
	except:
		logging.critical( 'Aborting discovery on %s server.',
		                  server[u'jid'], exc_info=sys.exc_info() )
		raise
	else:
		logging.info('Discovery Finished Succesfully')
	finally:
		_disconnect_clients(clients)
		#cl.Process(10)
		#for server in servers:
		#	show_node(server)
		#cl.disconnect()
	
	
	return servers
