#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# TalkTalkTalk
#
# is an easy-installable small chat room, with chat history. 
# 
# author:  Joseph Ernest (twitter: @JosephErnest)
# url:     http://github.com/josephernest/talktalktalk
# license: MIT license
# Updated for Python 3 by Matt McCright 2022-05-23

import sys
import json
import bleach
import time
import threading
# replaced dumbdbm with dbm:
import dbm
from dbm import dumb
import random
import re
import daemon
from bottle import route, run, view, request, post, ServerAdapter, get, static_file
from gevent import pywsgi
# install 'gevent-websocket'
# https://pypi.org/project/gevent-websocket/
# https://gitlab.com/noppo/gevent-websocket
from geventwebsocket.handler import WebSocketHandler
from geventwebsocket.exceptions import WebSocketError
from collections import deque
from config import PORT, HOST, ADMINNAME, ADMINHIDDENNAME, ALLOWEDTAGS

idx = 0


def websocket(callback):
    def wrapper(*args, **kwargs):
        callback(request.environ.get('wsgi.websocket'), *args, **kwargs)
    return wrapper


class GeventWebSocketServer(ServerAdapter):
    def run(self, handler):
        server = pywsgi.WSGIServer((self.host, self.port), handler, handler_class=WebSocketHandler)
        server.serve_forever()


def main():
    global idx
    # Original:
    # db = dumbdbm.open('talktalktalk.db', 'c')
    # Replacement 2022-05-23
    db = dbm.dumb.open('talktalktalk.db', 'c')
    idx = len(db)

    users = {}
    pings = {}
    user_message_times = {}

    def send_user_list():
        for u in users.keys():
            if not u.closed:
                u.send(json.dumps({'type': 'userlist', 'connected': users.values()}))

    def clean_username(usr, ws):
        username = bleach.clean(usr, tags=ALLOWEDTAGS, strip=True)
        # username = re.sub('[‍ :]', '', username)      # removes " ", ":", 
        # and the evil char "‍" http://unicode-table.com/fr/200D/
        username = re.sub(r'\W+', '', username)       # because of spam and usage of malicious utf8 characters, let's use alphanumeric usernames only for now
        username = username[:16]
        if username.lower() == ADMINNAME or username == '':
            username = 'user' + str(random.randint(0, 1000))
            ws.send(json.dumps({'type': 'usernameunavailable', 'username': username}))
        elif username.lower() == ADMINHIDDENNAME:
            username = ADMINNAME
            ws.send(json.dumps({'type': 'displayeduser', 'username': username}))
        return username            

    def db_worker():
        # when a user disappears during more than 30 seconds (+/- 10),
        # remove him/her from the userlist
        while True:
            user_list_changed = False
            t = time.time()
            for ws in users.copy():
                if t - pings[ws] > 30: 
                    del users[ws]
                    del pings[ws]
                    user_list_changed = True
            if user_list_changed:
                send_user_list()
            time.sleep(10)

    db_worker_thread = threading.Thread(target=db_worker)
    db_worker_thread.daemon = True
    db_worker_thread.start()

    @get('/ws', apply=[websocket])
    def chat(ws):
        global idx
        user_message_times[ws] = deque(maxlen=10)
        while True:
            try:
                received_message = ws.receive()
                if received_message is not None:

                    received_message = received_message.decode('utf8')        # ToDo: Deal with the decode error. McCright
                    if len(received_message) > 4096:      # this user is probably a spammer
                        ws.send(json.dumps({'type': 'flood'}))
                        break

                    pings[ws] = time.time()

                    if received_message == 'ping':         # ping/pong packet to make sure connection is still alive
                        ws.send('id' + str(idx-1))    # send the latest message id in return
                        if ws not in users:           # was deleted by db_worker
                            ws.send(json.dumps({'type': 'username'}))
                    else:
                        user_message_times[ws].append(time.time())                           # flood control
                        if len(user_message_times[ws]) == user_message_times[ws].maxlen:
                            if user_message_times[ws][-1] - user_message_times[ws][0] < 5:     # if more than 10 messages in 5 seconds (including ping messages)
                                ws.send(json.dumps({'type': 'flood'}))                    # disconnect the spammer
                                break

                        msg = json.loads(received_message)

                        if msg['type'] == 'message':
                            message = (bleach.clean(msg['message'], tags=ALLOWEDTAGS, strip=True)).strip()

                            if ws not in users:         # is this really mandatory ?
                                username = clean_username(msg['username'], ws)       
                                users[ws] = username
                                send_user_list()

                            if message:
                                if len(message) > 1000:
                                    message = message[:1000] + '...'
                                s = json.dumps({'type': 'message', 'message': message, 'username': users[ws], 'id': idx, 'datetime': int(time.time())})
                                db[str(idx)] = s                # Neither dumbdbm nor shelve module allow integer as key... I'm still looking for a better solution!
                                idx += 1
                                for u in users.keys():
                                    u.send(s)

                        elif msg['type'] == 'messages_before':
                            id_before = msg['id']
                            ws.send(json.dumps({'type': 'messages', 'before': 1, 'messages': [db[str(i)] for i in range(max(0,id_before - 100),id_before)]}))

                        elif msg['type'] == 'messages_after':
                            id_after = msg['id']
                            ws.send(json.dumps({'type': 'messages', 'before': 0, 'messages': [db[str(i)] for i in range(id_after,idx)]}))

                        elif msg['type'] == 'username':
                            username = clean_username(msg['username'], ws)
                            if ws not in users:          # welcome new user
                                ws.send(json.dumps({'type': 'messages', 'before': 0, 'messages': [db[str(i)] for i in range(max(0,idx - 100),idx)]}))
                            users[ws] = username
                            send_user_list()
                else:
                    break
            except (WebSocketError, ValueError, UnicodeDecodeError):      # ValueError happens for example when "No JSON object could be decoded", would be interesting to log it
                break

        if ws in users:
            del users[ws]
            del pings[ws]
            send_user_list()

    @route('/')
    @route('/index.html')
    @view('talktalktalk.html')
    def index():
        context = {'request': request}
        return context

    @route('/popsound.mp3')
    def popsound():
        return static_file('popsound.mp3', root='.')        

    run(host=HOST, port=PORT, debug=True, server=GeventWebSocketServer)


class talktalktalk(daemon.Daemon):
    def run(self):
        main()


if len(sys.argv) == 1:           # command line interactive mode
    main()

elif len(sys.argv) == 2:         # daemon mode
    daemon = talktalktalk(pidfile='_.pid', stdout='log.txt', stderr='log.txt')
   
    if 'start' == sys.argv[1]: 
        daemon.start()
    elif 'stop' == sys.argv[1]: 
        daemon.stop()
    elif 'restart' == sys.argv[1]: 
        daemon.restart()
        
