#!/usr/bin/env python
# -*- coding: iso-8859-15 -*-

# $id$

#
# Under GNU General Public License
#
# Author:   noalwin
# Email:    lambda512@gmail.com
# JabberID: lambda512@jabberes.com
#


# TODO: Make the code prettier, pylint



#jabberuser="my_user"
#jabberpassword="password"
#jabberresource="pybot"
#jabberserver="jabberes.org"

# Jabber account
JABBERUSER     = "xxxxxxx"
JABBERPASSWORD = "xxxxxxx"
JABBERRESOURCE = "pybot"
JABBERSERVER   = "xmpp.example.net"

# Database
DBUSER         = "user"
DBPASSWORD     = "sql_password"
DBHOST         = "localhost"
DBDATABASE     = "server_list"

# Server list
USEURL         = False
SERVERS_URL    = "http://www.jabber.org/basicservers.xml"
SERVERS_FILE   = "servers-fixed.xml"

# Logs
LOGFILE        = 'out.log'
LOGFILE        = None

#from xmpp import *
import logging
import pickle
import urllib
from xmpp import simplexml

from include import database_updater, html_file_generator, xmpp_discoverer


if LOGFILE is None:
	logging.basicConfig(
#	    level=logging.WARNING,
	    level=logging.DEBUG,
	    format='%(asctime)s %(levelname)s %(message)s'
	    )
else:
	logging.basicConfig(
	    level=logging.DEBUG,
	    format='%(asctime)s %(levelname)s %(message)s',
	    filename=LOGFILE,
	    filemode='w'
	    )



# Get server list

if USEURL:
	f = urllib.urlopen(SERVERS_URL)
else:
	f = open(SERVERS_FILE, 'r')

xmldata = f.read()
f.close()

node = simplexml.XML2Node(xmldata)

#items = node.getChildren()
items = node.getTags(name="item")

server_list = []

for item in items:
	if item.getAttr("jid") not in server_list:
		server_list.append(item.getAttr("jid"))

#servers=['jabberes.org', 'jab.undernet.cz', '12jabber.com', 'allchitchat.com', 'jabber.dk', 'amessage.be', 'jabber-hispano.org']

servers = xmpp_discoverer.discover_servers( JABBERUSER, JABBERPASSWORD,
                                            JABBERRESOURCE, JABBERSERVER,
                                            server_list
                                          )

#f = open('servers.dump', 'wb')
#pickle.dump(servers, f)

#f = open('servers.dump', 'rb')
#servers = pickle.load(f)

#f.close()

## TODO: delete this
#for server in servers:
	#server[u'available_services'] = server[u'availableServices']
	#del(server[u'availableServices'])
	#server[u'unavailable_services'] = server[u'unavailableServices']
	#del(server[u'unavailableServices'])

#for server in servers:
	#print
	#print 'Server: ' + server[u'jid']
	#print "Available:",
	#for service in server[u'available_services']:
		#print "\n " + service + " provided by:",
		#for jid in server[u'available_services'][service]:
			#print jid,
		
	#print "\nUnavailable:",
	#for service in server[u'unavailable_services']:
		#print "\n " + service + " provided by:",
		#for jid in server[u'unavailable_services'][service]:
			#print jid,
	#print ''


known_types = [ 'muc', 'irc', 'aim', 'gadu-gadu', 'http-ws', 'icq', 'msn', 'qq',
                'sms', 'smtp', 'tlen', 'yahoo', 'jud', 'pubsub', 'pep',
                'presence', 'newmail', 'rss', 'weather', 'proxy' ]

servers = database_updater.update_database( DBUSER, DBPASSWORD, DBHOST,
                                            DBDATABASE, servers, known_types
                                          )

#known_types.sort()
html_file_generator.generate('../servers-pybot.html', servers, known_types)
