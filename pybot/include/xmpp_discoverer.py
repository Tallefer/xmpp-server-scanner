
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
from random import choice
import re
import sys
import xml


from xmpp import Client, features

# Load the configuration
try:
	cfg = SafeConfigParser()
	cfg.readfp(open('config.cfg'))
	
	USE_MULTIPLE_QUERY_ACCOUNTS = cfg.getboolean("xmpp discoverer", "USE_MULTIPLE_QUERY_ACCOUNTS")
	ONLY_USE_SUCCESFULL_CLIENT = cfg.getboolean("xmpp discoverer", "ONLY_USE_SUCCESFULL_CLIENT")
	ONLY_RETRY_SERVERS = cfg.getboolean("xmpp discoverer", "ONLY_RETRY_SERVERS")
	INFO_QUERY_RETRIES = cfg.getint("xmpp discoverer", "INFO_QUERY_RETRIES")
	ITEM_QUERY_RETRIES = cfg.getint("xmpp discoverer", "ITEM_QUERY_RETRIES")
	
	del(cfg)
except:
	# Load default values
	
	# Use multiple accounts to query servers
	# When this option is set to True and a server does not answer it will retry
	# with another account
	# This will benefit accuracy on services detection over their availability as
	# seen by users
	# When set to False, only one randomly chosen account is used.
	# When False only use one account chosen at random. This will benefit accuracy
	# on detect the services availability as seen by users over the real service
	# availability
	USE_MULTIPLE_QUERY_ACCOUNTS = True
	
	# If a client can discover a server, only use that client to test services
	ONLY_USE_SUCCESFULL_CLIENT = True
	
	# Times to retry if a query fails
	# Not very useful if several accounts are used to do the discovery
	ONLY_RETRY_SERVERS = True
	INFO_QUERY_RETRIES = 1
	ITEM_QUERY_RETRIES = 0


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
	
	# Conference
	if ( jid.startswith((u'conference.', u'conf.', u'muc.', u'chat.', u'rooms.'))
	     and not ( '.yahoo.' in jid or '.irc.' in jid ) ):
		# MUC
		info = ( [{u'category': u'conference', u'type': u'text'}],
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
	elif jid.startswith((u'icq.', u'icqt.', u'jit-icq.', u'icq-jab.', u'icq2')):
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
	elif jid.startswith((u'presence.', u'webpresence.')):
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
	elif jid.startswith((u'file.', u'disk.', u'jdisk.')):
		info = ( [{u'category': u'store', u'type': u'file'}], [] )
	
	
	return info


def _handle_component_available(component, server):
	
	component['available'] = True
	
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
				_add_to_services_list( server[u'available_services'],
				                       ('conference', 'x-muc'), component )
			#continue
		
		# ejabberd1.1.3 uses pubsub:generic instead pubsub:service
		if identity[u'category']=='pubsub' and identity[u'type']=='generic':
			identity[u'type'] = 'service'
		
		# Some weather components use agent:weather instead headline:weather
		if identity[u'category']=='agent' and identity[u'type']=='weather':
			identity[u'category'] = 'headline'
		
		# PyRSS
		if identity[u'category']=='x-service' and identity[u'type']=='x-rss':
			identity[u'category'] = 'headline'
			identity[u'type'] = 'rss'
		
		_add_to_services_list(server[u'available_services'], (identity[u'category'], identity[u'type']), component)


def _handle_component_unavailable(component, server):
	
	component['available'] = False
	
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
				if not _in_same_domain(component[u'jid'], item[u'jid']):
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
		_handle_component_available(component, server)
		for identity in component[u'info'][0]:
			if ( (identity['category'] == u'server') | (
			        (identity['category'] == u'hierarchy') &
			        (identity['type'] == u'branch')
			   ) ):
				needs_to_query_items = True
	
	elif u'jabber:iq:agents' in component[u'info'][1]:
		#Fake identities. But we aren't really sure that it's a server?
		component[u'info'] = ( ({u'category': u'server', u'type': u'im'}),
		                     component[u'info'][1] )
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
		component[u'info'] = ( ({u'category': u'server', u'type': u'im'}),
		                       component[u'info'][1] )
	
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
			component[u'info'] = ( ({u'category': u'server', u'type': u'im'}),
			                       component[u'info'][1] )
		else:
			#try:
			_handle_component_available(component, server)
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
			elif u'node' in component:
				if (  (component[u'jid'] == item[u'jid']) &
					  (component[u'node'] != item[u'node'])  ):
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
			logging.error("Can not auth as %s@%s", (account['user'], account['server']))
			#raise IOError('Can not auth with server.')
			continue
		
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


def discover_servers(accounts, server_list):
	
	if USE_MULTIPLE_QUERY_ACCOUNTS:
		accounts = [choice(accounts)]
	
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