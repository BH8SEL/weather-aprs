#!/usr/bin/python

import os
import json, urllib2, string
from datetime import datetime
import threading
import socket
import Queue
import sys
import logging
import select
import errno
import itertools
from time import sleep
import re, ssl

ssl._create_default_https_context = ssl._create_unverified_context

Callsign = 'Bxxxxx-14'
Passcode = 'xxxxx'
Server = 's.aprs.cn:14580'
Protocal = 'any'
City = 'shanghai'
Key = 'aaaaaaaaaaaaaaaaaa2222222222'
SleepTime = 300

def bc():
 bcargs_weather = {
  'callsign': Callsign,
  'weather': 'https://free-api.heweather.com/v5/weather?lang=en&city=%s&key=%s' % (City, Key),
 }
 while True:
  frame = get_weather_frame(**bcargs_weather)
  if frame:
   ig.send(frame)

  sleep(SleepTime)

def process_ambiguity(pos, ambiguity):
 num = bytearray(pos)
 for i in range(0, ambiguity):
  if i > 1:
   # skip the dot
   i += 1
  # skip the direction
  i += 2
  num[-i] = " "
 return str(num)

def encode_lat(lat):
 lat_dir = 'N' if lat > 0 else 'S'
 lat_abs = abs(lat)
 lat_deg = int(lat_abs)
 lat_min = (lat_abs % 1) * 60
 return "%02i%05.2f%c" % (lat_deg, lat_min, lat_dir)

def encode_lng(lng):
 lng_dir = 'E' if lng > 0 else 'W'
 lng_abs = abs(lng)
 lng_deg = int(lng_abs)
 lng_min = (lng_abs % 1) * 60
 return "%03i%05.2f%c" % (lng_deg, lng_min, lng_dir)

def mkframe(callsign, payload):
 frame = APRSFrame()
 frame.source = callsign
 frame.dest = u'APRS'
 frame.path = [u'TCPIP*']
 frame.payload = payload
 return frame.export()

def get_beacon_frame(lat, lng, callsign, table, symbol, comment, ambiguity):
 enc_lat = process_ambiguity(encode_lat(lat), ambiguity)
 enc_lng = process_ambiguity(encode_lng(lng), ambiguity)
 pos = "%s%s%s" % (enc_lat, table, enc_lng)
 payload = "=%s%s%s" % (pos, symbol, comment)
 return mkframe(callsign, payload)

def get_status_frame(callsign, status):
 try:
  if status['file'] and os.path.exists(status['file']):
   status_text = open(status['file']).read().decode('UTF-8').strip()
  elif status['text']:
   status_text = status['text']
  else:
   return None
  payload = ">%s" % status_text
  return mkframe(callsign, payload)
 except:
  return None

def get_weather_frame(callsign, weather):
 try:
  req = urllib2.Request(weather)
  resp = urllib2.urlopen(req)
  wea_str = resp.read()

  if wea_str == -1: 
   print "Sorry that I can't find the weather info of this city now, please check or retry. weather string = %s " % wea_str 
   sys.exit(-1) 
  else: 
   w = json.loads(wea_str, encoding='utf-8')["HeWeather5"][0]
   
   timestamp = w['basic']['update']['utc']
      
   enc_lat = process_ambiguity(encode_lat(string.atof(w['basic']['lat'])), 0)
   enc_lng = process_ambiguity(encode_lng(string.atof(w['basic']['lon'])), 0)
   
   wenc = "%s%s%s" % (enc_lat, '/', enc_lng)
   
   # Wind
   wind = w['now'].get('wind', {})
   wenc += "_%03d" % string.atoi(wind['deg'])
   wenc += "/%03d" % string.atoi(wind['spd'])
   wenc += "g%03d" % 0
   cond = w['now']
   
   # Temperature
   wenc += "t%03d" % round(string.atoi(cond['tmp']) / (float(5)/9) + 32)
   wenc += "r%03d" % 0
   wenc += "p%03d" % round(string.atoi(cond['pcpn']) / 25.4)
   # Humidity
   if 'hum' in cond:
    wenc += "h%02d" % string.atoi(cond['hum'])
   else:
    wenc += "h..."
    
   # Atmospheric pressure
   if 'pres' in cond:
    wenc += "b%04d" % round(string.atoi(cond['pres']) * 10)
   else:
    wenc += "b..."
    
   # PM10, PM25 Info (add by ba7ib @ 2017-03-21)
   aqi = w['aqi']['city']
   wenc += ",...,...,...,000,%03d,%03d" % (string.atoi(aqi['pm10']), string.atoi(aqi['pm25']))
   #ch2o
   wenc += ",."
   #SunRise, SunSet, MoonRise, MoonSet Info (add by ba7ib @2017-03-21)
   df = w['daily_forecast'][0]['astro']
   payload = "=%s Air:%s SunR/S:%s/%s MoonR/S:%s/%s Info@UTC%s" % ( wenc, aqi['qlty'], df['sr'], df['ss'], df['mr'], df['ms'], timestamp)

   return mkframe(callsign, payload)
 except:
  return None

  class IGate:
 def __init__(self, callsign, passcode, gateways, preferred_protocol):
  if type(gateways) is list:
   self.gateways = itertools.cycle(gateways)
   self.gateway = False
  else:
   self.gateway = gateways
  self.callsign = callsign
  self.passcode = passcode
  self.preferred_protocol = preferred_protocol
  self.socket = None
  self._sending_queue = Queue.Queue(maxsize=1)
  self._connect()
  self._running = True
  self._worker = threading.Thread(target=self._socket_worker)
  self._worker.setDaemon(True)
  self._worker.start()

 def exit(self):
  self._running = False
  self._disconnect()

 def _connect(self):
  connected = False
  while not connected:
   try:
    gateway = self.gateway or next(self.gateways)
    if gateway.startswith("["):
     self.server, self.port = gateway.lstrip("[").split("]:")
    else:
     self.server, self.port = gateway.split(':')
    self.port = int(self.port)
    
    if self.preferred_protocol == 'ipv6':
     addrinfo = socket.getaddrinfo(self.server, self.port, socket.AF_INET6)
    elif self.preferred_protocol == 'ipv4':
     addrinfo = socket.getaddrinfo(self.server, self.port, socket.AF_INET)
    else:
     addrinfo = socket.getaddrinfo(self.server, self.port)
    self.socket = socket.socket(*addrinfo[0][0:3])
    self.socket.connect(addrinfo[0][4])
    server_hello = self.socket.recv(1024)
    version = 'GIT'
    self.socket.send("user %s pass %s vers PyMultimonAPRS %s filter r/38/-171/1\r\n" %
      (self.callsign, self.passcode, version))

    server_return = self.socket.recv(1024)
    connected = True
   except socket.error as e:
    sleep(1)

 def _disconnect(self):
  try:
   self.socket.close()
  except:
   pass

 def send(self, frame):
  try:
   self._sending_queue.put(frame, True, 10)
  except Queue.Full as e:
   print "Lost TX data (queue full): '%s'" % frame.export(False)
 def _socket_worker(self):
  while self._running:
   try:
    try:
     frame = self._sending_queue.get(True, 1)
     raw_frame = "%s\r\n" % frame
     totalsent = 0
     while totalsent < len(raw_frame):
      sent = self.socket.send(raw_frame[totalsent:])
      if sent == 0:
       raise socket.error(0, "Failed to send data - number of sent bytes: 0")
      totalsent += sent
    except Queue.Empty:
     pass
    self.socket.setblocking(0)
    try:
     res = self.socket.recv(40960)
    except socket.error as e:
     if not e.errno == 11:
      raise
    self.socket.setblocking(1)
   except socket.error as e:
    if e.errno == errno.EAGAIN or e.errno == errno.EWOULDBLOCK:
     sleep(1)
    else:
     sleep(1)
     self._connect()
  self.log.debug("sending thread exit")

header_re = re.compile(r'^(?P<source>\w*(-\d{1,2})?)>(?P<dest>\w*(-\d{1,2})?),(?P<path>[^\s]*)')

class InvalidFrame(Exception):
 pass

class APRSFrame:
 def __init__(self):
  self.source = None
  self.dest = None
  self.path = []
  self.payload = unicode()

 def import_tnc2(self, tnc2_frame, decode=True):
  if decode:
   tnc2_frame = tnc2_frame.decode('ISO-8859-1')
  tnc2_frame = tnc2_frame.replace("\r", "")
  header, payload = tnc2_frame.split(":", 1)
  header = header.strip()
  payload = payload.strip()
  try:
   res = header_re.match(header).groupdict()
   self.source = res['source']
   self.dest = res['dest']
   self.path = res['path'].split(',')
  except:
   raise InvalidFrame()
  self.payload = payload

 def export(self, encode=True):
  tnc2 = "%s>%s,%s:%s" % (self.source, self.dest, ','.join(self.path), self.payload)
  if len(tnc2) > 510:
   tnc2 = tnc2[:510]
  if encode:
   tnc2 = tnc2.encode('ISO-8859-1')
  return tnc2

ig = IGate(Callsign, Passcode, Server, Protocal)

# Start beacon in main thread
bc()
