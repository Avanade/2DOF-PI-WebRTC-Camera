import random
import ssl
import websockets
import asyncio
import os
import sys
import json
import argparse
import time
import gi
import threading
import socket
import re
try:
    import hashlib
    from urllib.parse import urlparse
except Exception as e:
    pass

try:
    import numpy as np
    import multiprocessing
    from multiprocessing import shared_memory
except Exception as e:
    pass

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GObject
gi.require_version('GstWebRTC', '1.0')
from gi.repository import GstWebRTC
gi.require_version('GstSdp', '1.0')
from gi.repository import GstSdp

try:
    from gi.repository import GLib
except:
    pass

def handle_unhandled_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    print("!!!  Unhandled exception !!! ", exc_type, exc_value, exc_traceback)
sys.excepthook = handle_unhandled_exception

def hex_to_ansi(hex_color):
    # Remove '#' character if present
    hex_color = hex_color.lstrip('#')

    # Convert hex to RGB
    if len(hex_color)==6:
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
    elif len(hex_color)==3:
        r = int(hex_color[0:1]+hex_color[0:1], 16)
        g = int(hex_color[1:2]+hex_color[1:2], 16)
        b = int(hex_color[2:3]+hex_color[2:3], 16)
    else:
        return hex_color

    # Calculate the nearest ANSI color
    ansi_color = 16 + (36 * int(r / 255 * 5)) + (6 * int(g / 255 * 5)) + int(b / 255 * 5)

    return f"\033[38;5;{ansi_color}m"

def printc(message, color_code=None):
    # ANSI escape code to reset to default text color
    reset_color = "\033[0m"

    if color_code is not None:
        color_code = hex_to_ansi(color_code)
        colored_message = f"{color_code}{message}{reset_color}"
        print(colored_message)
    else:
        print(message)

def printwin(message):
    printc("<= "+message,"93F")
def printwout(message):
    printc("=> "+message,"9F3")
def printin(message):
    printc("<= "+message,"F6A")
def printout(message):
    printc("=> "+message,"6F6")
def printwarn(message):
    printc(message,"FF0")

class WebRTCClient:
    def __init__(self, params):

        self.pipeline = params.pipeline
        self.conn = None
        self.pipe = None
        self.h264 = params.h264
        self.pipein = params.pipein
        self.bitrate = params.bitrate
        self.max_bitrate = params.bitrate
        self.server = params.server
        self.stream_id = params.streamid
        self.room_name = params.room
        self.multiviewer = params.multiviewer
        self.record = params.record
        self.streamin = params.streamin
        self.ndiout = params.ndiout
        self.fdsink = params.fdsink
        self.framebuffer = params.framebuffer
        self.midi = params.midi
        self.nored = params.nored
        self.noqos = params.noqos
        self.midi_thread = None
        self.midiout = None
        self.midiout_ports = None
        self.puuid = None
        self.clients = {}
        self.rotate = int(params.rotate)
        self.save_file = params.save
        self.noaudio = params.noaudio
        self.novideo = params.novideo
        self.counter = 0
        self.shared_memory = False
        self.trigger_socket = False
        self.processing = False
        self.buffer = params.buffer
        self.password = params.password
        self.hostname = params.hostname
        self.hashcode = ""
        self.aom = params.aom
        self.av1 = params.av1
       
    async def connect(self):
        print("Connecting to handshake server")
        sslctx = ssl.create_default_context()
        self.conn = await websockets.connect(self.server, ssl=sslctx)
        msg = json.dumps({"request":"seed","streamID":self.stream_id+self.hashcode})
        await self.conn.send(msg)
        printwout("seed start")
        
        
    def sendMessage(self, msg): # send message to wss
        if self.puuid:
            msg['from'] = self.puuid
        
        client = None
        if "UUID" in msg and msg['UUID'] in self.clients:
            client = self.clients[msg['UUID']]
        
        msg = json.dumps(msg)
        
        if client and client['send_channel']:
            try:
                client['send_channel'].emit('send-string', msg)
                printout("a message was sent via datachannels: "+msg[:20])
            except Exception as e:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.conn.send(msg))
                printwout("a message was sent via websockets 2: "+msg[:20])
        else:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self.conn.send(msg))
            printwout("a message was sent via websockets 1: "+msg[:20])
            #print("sent message wss")
        
    async def createPeer(self, UUID):
        
        if UUID in self.clients:
            client = self.clients[UUID]
        else:
            print("peer not yet created; error")
            return
            
        def on_offer_created(promise, _, __): 
            print("ON OFFER CREATED")
            promise.wait()
            reply = promise.get_reply()
            offer = reply.get_value('offer')
            promise = Gst.Promise.new()
            client['webrtc'].emit('set-local-description', offer, promise)
            promise.interrupt()
            print("SEND SDP OFFER")
            text = offer.sdp.as_text()
            msg = {'description': {'type': 'offer', 'sdp': text}, 'UUID': client['UUID'], 'session': client['session'], 'streamID':self.stream_id+self.hashcode}
            self.sendMessage(msg)

        def on_new_tranceiver(element, trans):
            print("ON NEW TRANS")
            #trans.set_property("fec-type", GstWebRTC.WebRTCFECType.ULP_RED)
            #trans.set_property("do-nack", True)

        def on_negotiation_needed(element):
            print("ON NEGO NEEDED")
            promise = Gst.Promise.new_with_change_func(on_offer_created, element, None)
            element.emit('create-offer', None, promise)

        def send_ice_local_candidate_message(_, mlineindex, candidate):
            if " TCP " in candidate: ##  I Can revisit another time, but for now, this isn't needed: TODO: optimize
                return
            icemsg = {'candidates': [{'candidate': candidate, 'sdpMLineIndex': mlineindex}], 'session':client['session'], 'type':'local', 'UUID':client['UUID']}
            self.sendMessage(icemsg)
                 
        def on_signaling_state(p1, p2):
            print("ON SIGNALING STATE CHANGE: {}".format(client['webrtc'].get_property(p2.name)))

        def on_ice_connection_state(p1, p2):
            if (client['webrtc'].get_property(p2.name)==1):
                printwarn("ice changed to checking state")
            elif (client['webrtc'].get_property(p2.name)==2):
                printwarn("ice changed to connected state")
            elif (client['webrtc'].get_property(p2.name)==3):
                printwarn("ice changed to completed state")
            elif (client['webrtc'].get_property(p2.name)>3):
                printc("ice changed to state +4","FC2")

        def on_connection_state(p1, p2):
            print("on_connection_state")
            
            if (client['webrtc'].get_property(p2.name)==2): # connected
                print("PEER CONNECTION ACTIVE")
                promise = Gst.Promise.new_with_change_func(on_stats, client['webrtc'], None) # check stats
                client['webrtc'].emit('get-stats', None, promise)
                
                if not client['send_channel']:
                    channel = client['webrtc'].emit('create-data-channel', 'sendChannel', None)
                    on_data_channel(client['webrtc'], channel)

                if client['timer'] == None:
                    client['ping'] = 0
                    client['timer'] = threading.Timer(3, pingTimer)
                    client['timer'].start()
                    self.clients[client["UUID"]] = client
                    
            elif (client['webrtc'].get_property(p2.name)>=4): # closed/failed , but this won't work unless Gstreamer / LibNice support it -- which isn't the case in most versions.
                print("PEER CONNECTION DISCONNECTED")
                self.stop_pipeline(client['UUID'])     
            else:
                print("PEER CONNECTION STATE {}".format(client['webrtc'].get_property(p2.name)))
                
        def pingTimer():
            
            if not client['send_channel']:
                client['timer'] = threading.Timer(3, pingTimer)
                client['timer'].start()
                print("data channel not setup yet")
                return
                
            if "ping" not in client:
                client['ping'] = 0
                
            if client['ping'] < 10:
                client['ping'] += 1
                self.clients[client["UUID"]] = client
                try:
                    client['send_channel'].emit('send-string', '{"ping":"'+str(time.time())+'"}')
                    printout("PINGED")
                except Exception as E:
                    print(E)
                    print("PING FAILED")
                client['timer'] = threading.Timer(3, pingTimer)
                client['timer'].start()

                promise = Gst.Promise.new_with_change_func(on_stats, client['webrtc'], None) # check stats
                client['webrtc'].emit('get-stats', None, promise)

            else:
                printc("NO HEARTBEAT", "F44")
                self.stop_pipeline(client['UUID'])
                
        def on_data_channel(webrtc, channel):
            print("    --------- ON DATA CHANNEL")
            if channel is None:
                print('DATA CHANNEL: NOT AVAILABLE')
                return
            else:
                print('DATA CHANNEL SETUP')                               
            channel.connect('on-open', on_data_channel_open)
            channel.connect('on-error', on_data_channel_error)
            channel.connect('on-close', on_data_channel_close)
            channel.connect('on-message-string', on_data_channel_message)

        def on_data_channel_error(arg1, arg2):
            printc('DATA CHANNEL: ERROR', "F44")

        def on_data_channel_open(channel):
            printc('DATA CHANNEL: OPENED', "06F")
            client['send_channel'] = channel
            self.clients[client["UUID"]] = client
            if self.rotate:
                msg = {"info":{"rotate_video":self.rotate}, "UUID": client["UUID"]}
                self.sendMessage(msg)

        def on_data_channel_close(channel):
            printc('DATA CHANNEL: CLOSE', "F44")

        def on_data_channel_message(channel, msg_raw):
            try:
                msg = json.loads(msg_raw)
            except:
                printin("DID NOT GET JSON")
                return
            if 'candidates' in msg:
                printin("INBOUND ICE BUNDLE - DC")
                for ice in msg['candidates']:
                    self.handle_sdp_ice(ice, client["UUID"])
            elif 'candidate' in msg:
                printin("INBOUND ICE SINGLE - DC")
                self.handle_sdp_ice(msg, client["UUID"])
            elif 'pong' in msg: # Supported in v19 of VDO.Ninja
                printin('PONG')
                client['ping'] = 0     
                self.clients[client["UUID"]] = client                
            elif 'bye' in msg: ## v19 of VDO.Ninja
                printin("PEER INTENTIONALLY HUNG UP")
                #self.stop_pipeline(client['UUID'])
            elif 'description' in msg:
                printin("INCOMING SDP - DC")
                if msg['description']['type'] == "offer":
                    self.handle_offer(msg['description'], client['UUID'])
            elif 'bitrate' in msg:
                printin(msg)
                if client['encoder'] and msg['bitrate']:
                    print("Trying to change bitrate...")
                    if self.aom:
                        print("Aom doesn't support dynamic bitrates currently")
                        pass
                       # client['encoder'].set_property('target-bitrate', int(msg['bitrate']))
                    else:
                        client['encoder'].set_property('bitrate', int(msg['bitrate'])*1000)
            else:
                printin("MISC DC DATA")
                return

        def on_stats(promise, abin, data):
            promise.wait()
            stats = promise.get_reply()
            stats = stats.to_string()
            stats = stats.replace("\\", "")
            stats = stats.split("fraction-lost=(double)")
            if (len(stats)>1):
                stats = stats[1].split(",")[0]
                print("Packet loss:"+stats)
                if " vp8enc " in self.pipeline: # doesn't support dynamic bitrates? not sure the property to use at least
                    return
                elif " av1enc " in self.pipeline: # seg-fault if I try to change it currently
                    return
                stats = float(stats)
                if (stats>0.01) and not self.noqos:
                    print("Trying to reduce change bitrate...")
                    bitrate = self.bitrate*0.9
                    if bitrate < self.max_bitrate*0.2:
                        bitrate = self.max_bitrate*0.2
                    elif bitrate > self.max_bitrate*0.8:
                        bitrate = self.bitrate*0.9
                    self.bitrate = bitrate
                    print(str(bitrate))
                    try:
                        if self.aom:
                            client['encoder'].set_property('target-bitrate', int(bitrate))  # line not active due to 'elif " av1enc " in self.pipeline:' line
                        elif client['encoder']:
                            client['encoder'].set_property('bitrate', int(bitrate*1000))
                        elif client['encoder1']:
                            client['encoder1'].set_property('bitrate', int(bitrate))
                        elif client['encoder2']:
                            pass
                            # client['encoder2'].set_property('extra-controls',"controls,video_bitrate="+str(bitrate)+"000;") ## sadly, this is set on startup

                    except Exception as E:
                        print(E)
                elif (stats<0.003) and not self.noqos:
                    print("Trying to increase change bitrate...")
                    bitrate = self.bitrate*1.05
                    if bitrate>self.max_bitrate:
                        bitrate = self.max_bitrate
                    elif bitrate*2<self.max_bitrate:
                        bitrate = self.bitrate*1.05
                    self.bitrate = bitrate
                    print(str(bitrate))
                    try:
                        if self.aom:
                            client['encoder'].set_property('target-bitrate', int(bitrate))  # line not active due to 'elif " av1enc " in self.pipeline:' line
                        elif client['encoder']:
                            client['encoder'].set_property('bitrate', int(bitrate*1000))
                        elif client['encoder1']:
                            client['encoder1'].set_property('bitrate', int(bitrate))
                        elif client['encoder2']:
                            pass
                            # client['encoder2'].set_property('extra-controls',"controls,video_bitrate="+str(bitrate)+"000;")  ## set on startup; can't change
                    except Exception as E:
                        print(E)

        print("creating a new webrtc bin")
        
        started = True
        if not self.pipe:
            print("loading pipe")
           
            if self.streamin:
                self.pipe = Gst.Pipeline.new('decode-pipeline') ## decode or capture 
            elif len(self.pipeline)<=1:
                self.pipe = Gst.Pipeline.new('data-only-pipeline')
            else:
                print(self.pipeline)
                self.pipe = Gst.parse_launch(self.pipeline)
            print(self.pipe)
            started = False
            

        client['webrtc'] = self.pipe.get_by_name('sendrecv')
        client['qv'] = None
        client['qa'] = None
        client['encoder'] = False
        client['encoder1'] = False
        client['encoder2'] = False
        try:
            client['encoder'] = self.pipe.get_by_name('encoder')
        except:
            try:
                client['encoder1'] = self.pipe.get_by_name('encoder1')
            except:
                try:
                    client['encoder2'] = self.pipe.get_by_name('encoder2')
                except:
                    pass

        if (not self.multiviewer) and client['webrtc']:
            pass
        else:
            client['webrtc'] = Gst.ElementFactory.make("webrtcbin", client['UUID'])
            client['webrtc'].set_property('bundle-policy', 'max-bundle') 
            client['webrtc'].set_property('stun-server', "stun://stun4.l.google.com:19302")  ## older versions of gstreamer might break with this
            #client['webrtc'].set_property('turn-server', 'turn://vdoninja:IchBinSteveDerNinja@www.turn.vdo.ninja:3478') # temporarily hard-coded
            try:
                client['webrtc'].set_property('latency', self.buffer)
                client['webrtc'].set_property('async-handling', True)
            except:
                pass
            self.pipe.add(client['webrtc'])
            
            atee = self.pipe.get_by_name('audiotee')
            vtee = self.pipe.get_by_name('videotee')

            if vtee is not None:
                qv = Gst.ElementFactory.make('queue', f"qv-{client['UUID']}")
                self.pipe.add(qv)
                if not Gst.Element.link(vtee, qv):
                    return
                if not Gst.Element.link(qv, client['webrtc']):
                    return
                if qv is not None: qv.sync_state_with_parent()
                client['qv'] = qv
            
            if atee is not None:
                qa = Gst.ElementFactory.make('queue', f"qa-{client['UUID']}")
                self.pipe.add(qa)
                if not Gst.Element.link(atee, qa):
                    return
                if not Gst.Element.link(qa, client['webrtc']):
                    return
                if qa is not None: qa.sync_state_with_parent()
                client['qa'] = qa
        try:
            client['webrtc'].connect('notify::ice-connection-state', on_ice_connection_state)
            client['webrtc'].connect('notify::connection-state', on_connection_state)
            client['webrtc'].connect('notify::signaling-state', on_signaling_state)
                
        except Exception as e:
            print(e)
            pass
            
        client['webrtc'].connect('on-ice-candidate', send_ice_local_candidate_message)
        client['webrtc'].connect('on-negotiation-needed', on_negotiation_needed)
        client['webrtc'].connect('on-new-transceiver', on_new_tranceiver)
        #client['webrtc'].connect('on-data-channel', on_data_channel)
 
        try:
            if not self.streamin:
                trans = client['webrtc'].emit("get-transceiver",0)
                if trans is not None:
                    try:
                        if not self.nored:
                            trans.set_property("fec-type", GstWebRTC.WebRTCFECType.ULP_RED)
                            print("FEC ENABLED")
                    except:
                        pass
                    trans.set_property("do-nack", True)
                    print("SEND NACKS ENABLED")

        except Exception as E:
            print(E)


        if not started and self.pipe.get_state(0)[1] is not Gst.State.PLAYING:
            self.pipe.set_state(Gst.State.PLAYING)
                           
        client['webrtc'].sync_state_with_parent()

        if not client['send_channel']:
            channel = client['webrtc'].emit('create-data-channel', 'sendChannel', None)
            on_data_channel(client['webrtc'], channel)

        self.clients[client["UUID"]] = client

        
    def handle_sdp_ice(self, msg, UUID):
        client = self.clients[UUID]
        if not client or not client['webrtc']:
            print("! CLIENT NOT FOUND OR INVALID")
            return
        
        if 'sdp' in msg:
            print("INCOMING ANSWER SDP TYPE: "+msg['type'])
            assert(msg['type'] == 'answer')
            sdp = msg['sdp']

            res, sdpmsg = GstSdp.SDPMessage.new()
            GstSdp.sdp_message_parse_buffer(bytes(sdp.encode()), sdpmsg)
            answer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg)
            promise = Gst.Promise.new()
            client['webrtc'].emit('set-remote-description', answer, promise)
            promise.interrupt()
        elif 'candidate' in msg:
            print("  ~ HANDLING INBOUND ICE")
            candidate = msg['candidate']
            sdpmlineindex = msg['sdpMLineIndex']
            client['webrtc'].emit('add-ice-candidate', sdpmlineindex, candidate)
        else:
            print(msg)
            print("UNEXPECTED INCOMING")
      
    async def start_pipeline(self, UUID):
        print("START PIPE")
        if self.multiviewer:
            await self.createPeer(UUID)
        else:
            for uid in self.clients:
                if uid != UUID:
                    print("Consider --multiviewer mode if you need multiple viewers")
                    self.stop_pipeline(uid)
                    break
            if self.pipe:
                print("setting pipe to null")
                self.pipe.set_state(Gst.State.NULL)
                self.pipe = None
                
                if UUID in self.clients:
                    print("Resetting existing pipe and p2p connection.");
                    session = self.clients[UUID]["session"]
                    
                    if self.clients[UUID]['timer']:
                        self.clients[UUID]['timer'].cancel()
                        print("stop previous ping/pong timer")
                    
                    self.clients[UUID] = {}
                    self.clients[UUID]["UUID"] = UUID
                    self.clients[UUID]["session"] = session
                    self.clients[UUID]["send_channel"] = None
                    
                    self.clients[UUID]["timer"] = None
                    self.clients[UUID]["ping"] = 0
                    self.clients[UUID]["webrtc"] = None
                    
            await self.createPeer(UUID)

    def stop_pipeline(self, UUID):
        print("STOP PIPE")
        if not self.multiviewer:
            if UUID in self.clients:
                del self.clients[UUID]
        elif UUID in self.clients:
            atee = self.pipe.get_by_name('audiotee')
            vtee = self.pipe.get_by_name('videotee')

            if atee is not None and self.clients[UUID]['qa'] is not None: 
                atee.unlink(self.clients[UUID]['qa'])
            if vtee is not None and self.clients[UUID]['qv'] is not None: 
                vtee.unlink(self.clients[UUID]['qv'])

            if self.clients[UUID]['webrtc'] is not None: 
                self.pipe.remove(self.clients[UUID]['webrtc'])
                self.clients[UUID]['webrtc'].set_state(Gst.State.NULL)
            if self.clients[UUID]['qa'] is not None: 
                self.pipe.remove(self.clients[UUID]['qa'])
                self.clients[UUID]['qa'].set_state(Gst.State.NULL)
            if self.clients[UUID]['qv'] is not None:
                self.pipe.remove(self.clients[UUID]['qv'])
                self.clients[UUID]['qv'].set_state(Gst.State.NULL)
            del self.clients[UUID]


        if self.pipe:
            if len(self.clients)==0:
                self.pipe.set_state(Gst.State.NULL)
                self.pipe = None
            
    async def loop(self):
        assert self.conn
        print("WSS CONNECTED")
        async for message in self.conn:
            try:
                msg = json.loads(message)
                
                if 'UUID' in msg:
                    if (self.puuid != None) and (self.puuid != msg['UUID']):
                        continue
                    UUID = msg['UUID']
                else:
                    continue
                    
                if UUID not in self.clients:
                    self.clients[UUID] = {}
                    self.clients[UUID]["UUID"] = UUID
                    self.clients[UUID]["session"] = None
                    self.clients[UUID]["send_channel"] = None
                    self.clients[UUID]["timer"] = None
                    self.clients[UUID]["ping"] = 0
                    self.clients[UUID]["webrtc"] = None
                    
                if 'session' in msg:
                    if not self.clients[UUID]['session']:
                        self.clients[UUID]['session'] = msg['session']
                    elif self.clients[UUID]['session'] != msg['session']:
                        print("sessions don't match")

                if 'description' in msg:
                    print("description via WSS")
                    msg = msg['description']
                    if 'type' in msg:
                        if msg['type'] == "answer":
                            self.handle_sdp_ice(msg, UUID)
                            
                elif 'candidates' in msg:
                    print("ice candidates BUNDLE via WSS")
                    if type(msg['candidates']) is list:
                        for ice in msg['candidates']:
                            self.handle_sdp_ice(ice, UUID)
                            
                elif 'candidate' in msg:
                    print("ice candidate SINGLE via WSS")
                    self.handle_sdp_ice(msg, UUID)
                    
                elif 'request' in msg:
                    print("REQUEST via WSS: ", msg['request'])
                    if 'offerSDP' in  msg['request']:
                        await self.start_pipeline(UUID)
                    elif msg['request'] == "play":
                        if self.puuid==None:
                            self.puuid = str(random.randint(10000000,99999999))
                        if 'streamID' in msg:
                            if msg['streamID'] == self.stream_id+self.hashcode:
                                await self.start_pipeline(UUID)
            except websockets.ConnectionClosed:
                print("WEB SOCKETS CLOSED; retrying in 5s");
                await asyncio.sleep(5)
                continue
            except Exception as E:
                print(E)
        return 0


def check_plugins(needed):
    missing = list(filter(lambda p: Gst.Registry.get().find_plugin(p) is None, needed))
    if len(missing):
        print('Missing gstreamer plugins:', missing)
        return False
    return True

def on_message(bus: Gst.Bus, message: Gst.Message, loop):
    mtype = message.type
    """
        Gstreamer Message Types and how to parse
        https://lazka.github.io/pgi-docs/Gst-1.0/flags.html#Gst.MessageType
    """
    if not loop:
        try:
            loop = GLib.MainLoop
        except:
            loop = GObject.MainLoop

    if mtype == Gst.MessageType.EOS:
        print("End of stream")
        loop.quit()

    elif mtype == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        print(err, debug)
        loop.quit()

    elif mtype == Gst.MessageType.WARNING:
        err, debug = message.parse_warning()
        print(err, debug)
        
    elif mtype == Gst.MessageType.LATENCY:  # needs self. scope added
        print("LATENCY")
        # self.pipe.recalculate_latency() 
        
    elif mtype == Gst.MessageType.ELEMENT:
        print("ELEMENT")

    return True


WSS="wss://wss.vdo.ninja:443"

async def main():

    error = False
    parser = argparse.ArgumentParser()
    parser.add_argument('--streamid', type=str, default=str(random.randint(1000000,9999999)), help='Stream ID of the peer to connect to')
    parser.add_argument('--room', type=str, default=None, help='optional - Room name of the peer to join')
    parser.add_argument('--rtmp', type=str, default=None, help='Use RTMP instead; pass the rtmp:// publishing address here to use')
    parser.add_argument('--whip', type=str, default=None, help='Use WHIP output instead; pass the https://whip.publishing/address here to use')
    parser.add_argument('--server', type=str, default=None, help='Handshake server to use, eg: "wss://wss.vdo.ninja:443"')
    parser.add_argument('--bitrate', type=int, default=2500, help='Sets the video bitrate; kbps. If error correction (red) is on, the total bandwidth used may be up to 2X higher than the bitrate')
    parser.add_argument('--audiobitrate', type=int, default=64, help='Sets the audio bitrate; kbps.')
    parser.add_argument('--width', type=int, default=1920, help='Sets the video width. Make sure that your input supports it.')
    parser.add_argument('--height', type=int, default=1080, help='Sets the video height. Make sure that your input supports it.')
    parser.add_argument('--framerate', type=int, default=30, help='Sets the video framerate. Make sure that your input supports it.')
    parser.add_argument('--test', action='store_true', help='Use test sources.')
    parser.add_argument('--hdmi', action='store_true', help='Try to setup a HDMI dongle')
    parser.add_argument('--camlink', action='store_true', help='Try to setup an Elgato Cam Link')
    parser.add_argument('--z1', action='store_true', help='Try to setup a Theta Z1 360 camera')
    parser.add_argument('--z1passthru', action='store_true', help='Try to setup a Theta Z1 360 camera, but do not transcode')
    parser.add_argument('--v4l2', type=str, default=None, help='Sets the V4L2 input device.')
    parser.add_argument('--libcamera', action='store_true',  help='Use libcamera as the input source')
    parser.add_argument('--rpicam', action='store_true', help='Sets the RaspberryPi CSI input device. If this fails, try --rpi --raw or just --raw instead.')
    parser.add_argument('--rotate', type=int, default=0, help='Rotates the camera in degrees; 0 (default), 90, 180, 270 are possible values.')
    parser.add_argument('--nvidiacsi', action='store_true', help='Sets the input to the nvidia csi port.')
    parser.add_argument('--alsa', type=str, default=None, help='Use alsa audio input.')
    parser.add_argument('--pulse', type=str, help='Use pulse audio (or pipewire) input.')
    parser.add_argument('--zerolatency', action='store_true', help='A mode designed for the lowest audio output latency')
    parser.add_argument('--raw', action='store_true', help='Opens the V4L2 device with raw capabilities.')
    parser.add_argument('--bt601', action='store_true', help='Use colormetery bt601 mode; enables raw mode also')
    parser.add_argument('--h264', action='store_true', help='Prioritize h264 over vp8')
    parser.add_argument('--x264', action='store_true', help='Prioritizes x264 encoder over hardware encoder')
    parser.add_argument('--openh264', action='store_true', help='Prioritizes OpenH264 encoder over hardware encoder')
    parser.add_argument('--vp8', action='store_true', help='Prioritizes vp8 codec over h264; software encoder')
    parser.add_argument('--vp9', action='store_true', help='Prioritizes vp9 codec over h264; software encoder')
    parser.add_argument('--aom', action='store_true', help='Prioritizes AV1-AOM codec; software encoder')
    parser.add_argument('--av1', action='store_true', help='Auto selects an AV1 codec for encoding; hardware or software')
    parser.add_argument('--rav1e', action='store_true', help='rav1e AV1 encoder used')
    parser.add_argument('--qsv', action='store_true', help='Intel quicksync AV1 encoder used')
    parser.add_argument('--omx', action='store_true', help='Try to use the OMX driver for encoding video; not recommended')
    parser.add_argument('--vorbis', action='store_true', help='Try to use the OMX driver for encoding video; not recommended')
    parser.add_argument('--nvidia', action='store_true', help='Creates a pipeline optimised for nvidia hardware.')
    parser.add_argument('--rpi', action='store_true', help='Creates a pipeline optimised for raspberry pi hadware.')
    parser.add_argument('--multiviewer', action='store_true', help='Allows for multiple viewers to watch a single encoded stream; will use more CPU and bandwidth.')
    parser.add_argument('--noqos', action='store_true', help='Do not try to automatically reduce video bitrate if packet loss gets too high. The default will reduce the bitrate if needed.')
    parser.add_argument('--nored', action='store_true', help='Disable error correction redundency for transmitted video. This may reduce the bandwidth used by half, but it will be more sensitive to packet loss')
    parser.add_argument('--novideo', action='store_true', help='Disables video input.')
    parser.add_argument('--noaudio', action='store_true', help='Disables audio input.')
    parser.add_argument('--led', action='store_true', help='Enable GPIO pin 12 as an LED indicator light; for Raspberry Pi.')
    parser.add_argument('--pipeline', type=str, help='A full custom pipeline')
    parser.add_argument('--record',  type=str, help='Specify a stream ID to record to disk. System will not publish a stream when enabled.') ### Doens't work correctly yet. might be a gstreamer limitation.
    parser.add_argument('--save', action='store_true', help='Save a copy of the outbound stream to disk. Publish Live + Store the video.')
    parser.add_argument('--midi', action='store_true', help='Transparent MIDI bridge mode; no video or audio.')
    parser.add_argument('--filesrc', type=str, default=None,  help='Provide a media file (local file location) as a source instead of physical device; it can be a transparent webm or whatever. It will be transcoded, which offers the best results.')
    parser.add_argument('--filesrc2', type=str, default=None,  help='Provide a media file (local file location) as a source instead of physical device; it can be a transparent webm or whatever. It will not be transcoded, so be sure its encoded correctly. Specify if --vp8 or --vp9, else --h264 is assumed.')
    parser.add_argument('--pipein', type=str, default=None, help='Pipe a media stream in as the input source. Pass `auto` for auto-decode,pass codec type for pass-thru (mpegts,h264,vp8,vp9), or use `raw`'); 
    parser.add_argument('--ndiout',  type=str, help='VDO.Ninja to NDI output; requires the NDI Gstreamer plugin installed')
    parser.add_argument('--fdsink',  type=str, help='VDO.Ninja to the stdout pipe; common for piping data between command line processes')
    parser.add_argument('--framebuffer', type=str, help='VDO.Ninja to local frame buffer; performant and Numpy/OpenCV friendly')
    parser.add_argument('--debug', action='store_true', help='Show added debug information from Gsteamer and other aspects of the app')
    parser.add_argument('--buffer',  type=int, default=200, help='The jitter buffer latency in milliseconds; default is 200ms. (gst +v1.18)')
    parser.add_argument('--password', type=str, nargs='?', default=None, required=False, const='', help='Partial password support. If not used, passwords will be off. If a blank value is passed, it will use the default system password. If you pass a value, it will use that value for the pass. No added encryption support however. Works for publishing to vdo.ninja/alpha/ (v24) currently')
    parser.add_argument('--hostname', type=str, default='https://vdo.ninja/alpha/', help='Your URL for vdo.ninja, if self-hosting the website code')

    args = parser.parse_args()
    
    Gst.init(None)
    Gst.debug_set_active(False)
    Gst.debug_set_default_threshold(0)
     
    if Gst.Registry.get().find_plugin("rpicamsrc"):  args.rpi=True

    PIPELINE_DESC = ""
    needed = ["nice", "webrtc", "dtls", "srtp", "rtp", "sctp", "rtpmanager"]
    h264 = list(filter(lambda p: Gst.Registry.get().find_plugin(p) is None, ['x264', 'openh264']))

    if 'openh264' not in h264:
        h264 = "openh264"
        print("openh264")
    elif 'x264' not in h264:
        h264 = "x264"
        print("x264")
    else:
        h264 = False
        print("no h264")

       
    audiodevices = [] 
    args.noaudio = True

    if args.rpicam:
        print("Please note: If rpicamsrc cannot be found, use --libcamera instead")
        if not check_plugins(['rpicamsrc']):
            print("rpicamsrc was not found. using just --rpi instead")
            print()
            args.raw = True
            args.rpi = True
            args.rpicam = False
            
 
    if args.rpi and not args.v4l2 and not args.rpicam:
        if check_plugins(['libcamera']):
            args.libcamera = True

        monitor = Gst.DeviceMonitor.new()
        monitor.add_filter("Video/Source", None)
        devices = monitor.get_devices()
        for d in devices:
            cam = d.get_display_name()
            if "-isp" in cam:
                continue
            print("Video device found: "+d.get_display_name())
        print("")
   
        picam = [d for d in devices if "Raspberry Pi Camera Module" in  d.get_display_name()]
        if len(picam):
            args.rpicam = True

        usbcam = [d for d in devices if "USB Vid" in  d.get_display_name()]
        
        if len(usbcam) and not args.v4l2:
            args.v4l2 = "/dev/video0"  

    elif not args.v4l2:
        args.v4l2 = '/dev/video0'
    
    if args.pipeline is not None:
        PIPELINE_DESC = args.pipeline
        print('We assume you have tested your custom pipeline with: gst-launch-1.0 ' + args.pipeline.replace('(', '\\(').replace('(', '\\)'))
    else:
        pipeline_video_input = ''
        pipeline_audio_input = ''


        if args.nvidia or args.rpi or args.x264 or args.openh264:
            args.h264 = True


        args.multiviewer = True
        saveVideo = ""
        args.streamin = False

        if not args.novideo:

            if not (args.rpi) and args.h264:
                if args.x264:
                    needed += ['x264']
                elif args.openh264:
                    needed += ['openh264']
                elif args.omx:
                    needed += ['omx']
                elif h264:
                    needed += ['h264']
                elif args.rpicam:
                    needed += ['rpicamsrc']
                else:
                    print("Is there an H264 encoder installed?")

            if args.rpi and not args.rpicam:
                needed += ['video4linux2']
                if args.x264:
                    needed += ['x264']
                elif args.openh264:
                    needed += ['openh264']
                elif args.omx:
                    needed += ['omx']
                elif h264:
                    needed += [h264]
                else:
                    print("Is there an H264 encoder installed?")

                if not args.raw:
                    needed += ['jpeg']

            if args.test:
                needed += ['videotestsrc']
                pipeline_video_input = 'videotestsrc'
                if args.nvidia:
                    pipeline_video_input = f'videotestsrc ! video/x-raw,width=(int){args.width},height=(int){args.height},format=(string)NV12,framerate=(fraction){args.framerate}/1'
                else:
                    pipeline_video_input = f'videotestsrc ! video/x-raw,width=(int){args.width},height=(int){args.height},type=video,framerate=(fraction){args.framerate}/1'

            elif args.rpicam:
                needed += ['rpicamsrc']
                args.rpi = True
                
                rotate = ""
                if args.rotate:
                    rotate = " rotation="+str(int(args.rotate))
                    args.rotate = 0
                pipeline_video_input = f'rpicamsrc bitrate={args.bitrate}000{rotate} ! video/x-h264,profile=constrained-baseline,width={args.width},height={args.height},framerate=(fraction){args.framerate}/1,level=3.0 ! queue max-size-time=1000000000  max-size-bytes=10000000000 max-size-buffers=1000000 '

            elif args.libcamera:
                needed += ['libcamera']
                pipeline_video_input = f'libcamerasrc'
                if args.aom:
                    pipeline_video_input += f' ! video/x-raw,width=(int){args.width},height=(int){args.height},format=(string)I420,framerate=(fraction){args.framerate}/1' # might not be compatible with all video sources, but its compatible with AOM. lower CPU usage this way
                else:
                    pipeline_video_input += f' ! video/x-raw,width=(int){args.width},height=(int){args.height},format=(string)YUY2,framerate=(fraction){args.framerate}/1'
            elif args.v4l2:
                needed += ['video4linux2']
                pipeline_video_input = f'v4l2src device={args.v4l2} io-mode=2 do-timestamp=true'
                if not os.path.exists(args.v4l2):
                    print(f"The video input {args.v4l2} does not exists.")
                    error = True
                elif not os.access(args.v4l2, os.R_OK):
                    print(f"The video input {args.v4l2} does exists, but no persmissions te read.")
                    error = True

                if args.raw:
                    if args.rpi:
                        if args.bt601:
                            pipeline_video_input += f' ! video/x-raw,width=(int){args.width},height=(int){args.height},type=video,framerate=(fraction){args.framerate}/1,colorimetry=(string)bt601'  ## bt601 is another option,etc.
                        else:
                            pipeline_video_input += f' ! video/x-raw,width=(int){args.width},height=(int){args.height},type=video,framerate=(fraction){args.framerate}/1 ! capssetter caps="video/x-raw,format=YUY2,colorimetry=(string)2:4:5:4"'

                    else:
                        pipeline_video_input += f' ! video/x-raw,width=(int){args.width},height=(int){args.height},type=video,framerate=(fraction){args.framerate}/1'

                else:
                    pipeline_video_input += f' ! image/jpeg,width=(int){args.width},height=(int){args.height},type=video,framerate=(fraction){args.framerate}/1'
                    if args.nvidia:
                        pipeline_video_input += ' ! jpegparse ! nvjpegdec ! video/x-raw'
                    elif args.rpi:
                        pipeline_video_input += ' ! jpegparse ! v4l2jpegdec '
                    else:
                        pipeline_video_input += ' ! jpegdec'

            if args.filesrc2:
                pass
            elif args.h264:
                # H264
                if args.rpicam:
                    pass
                elif args.rpi:
                    if args.x264:
                        pipeline_video_input += f' ! v4l2convert ! video/x-raw,format=I420 ! queue max-size-buffers=10 ! x264enc  name="encoder1" bitrate={args.bitrate} speed-preset=1 tune=zerolatency qos=true ! video/x-h264,profile=constrained-baseline,stream-format=(string)byte-stream'
                    elif args.openh264:
                        pipeline_video_input += f' ! v4l2convert ! video/x-raw,format=I420 ! queue max-size-buffers=10 ! openh264enc  name="encoder" bitrate={args.bitrate}000 complexity=0 ! video/x-h264,profile=constrained-baseline,stream-format=(string)byte-stream'
                    else:
                        pipeline_video_input += f' ! v4l2convert ! videorate ! video/x-raw,format=I420 ! v4l2h264enc extra-controls="controls,video_bitrate={args.bitrate}000;" qos=true name="encoder2" ! video/x-h264,level=(string)4' ## v4l2h264enc only supports 30fps max @ 1080p on most rpis, and there might be a spike or skipped frame causing the encode to fail; videorating it seems to fix it though

                elif h264=="x264":
                    pipeline_video_input += f' ! videoconvert ! queue max-size-buffers=10 ! x264enc bitrate={args.bitrate} name="encoder1" speed-preset=1 tune=zerolatency qos=true ! video/x-h264,profile=constrained-baseline'
                elif h264=="openh264":
                    pipeline_video_input += f' ! videoconvert ! queue max-size-buffers=10 ! openh264enc bitrate={args.bitrate}000 name="encoder" complexity=0 ! video/x-h264,profile=constrained-baseline'
                else:
                    pipeline_video_input += f' ! v4l2convert ! video/x-raw,format=I420 ! omxh264enc name="encoder" target-bitrate={args.bitrate}000 qos=true control-rate=1 ! video/x-h264,stream-format=(string)byte-stream' ## Good for a RPI Zero I guess?
                    
                pipeline_video_input += f' ! queue max-size-time=1000000000  max-size-bytes=10000000000 max-size-buffers=1000000 ! h264parse ! rtph264pay config-interval=-1 aggregate-mode=zero-latency ! application/x-rtp,media=video,encoding-name=H264,payload=96'

            else:
                # VP8
                if args.nvidia:
                    pipeline_video_input += f' ! nvvidconv ! video/x-raw(memory:NVMM) ! omxvp8enc bitrate={args.bitrate}000 control-rate="constant" name="encoder" qos=true ! rtpvp8pay ! application/x-rtp,media=video,encoding-name=VP8,payload=96'
                elif args.rpi:
                    pipeline_video_input += f' ! v4l2convert ! video/x-raw,format=I420 ! queue max-size-buffers=10 ! vp8enc deadline=1 name="encoder" target-bitrate={args.bitrate}000 {saveVideo} ! rtpvp8pay ! application/x-rtp,media=video,encoding-name=VP8,payload=96'
                # need to add an nvidia vp8 hardware encoder option.
                else:
                    pipeline_video_input += f' ! videoconvert ! queue max-size-buffers=10 ! vp8enc deadline=1 target-bitrate={args.bitrate}000 name="encoder" {saveVideo} ! rtpvp8pay ! application/x-rtp,media=video,encoding-name=VP8,payload=96'
                    
            if args.multiviewer:
                pipeline_video_input += ' ! tee name=videotee '
            else:
                pipeline_video_input += ' ! queue ! sendrecv. '


        if not args.multiviewer:
            if Gst.version().minor >= 18:
                PIPELINE_DESC = f'webrtcbin name=sendrecv async-handling=true stun-server=stun://stun4.l.google.com:19302 bundle-policy=max-bundle {pipeline_video_input} {pipeline_audio_input} {pipeline_save}'
            else: ## oldvers v1.16 options  non-advanced options
                PIPELINE_DESC = f'webrtcbin name=sendrecv stun-server=stun://stun4.l.google.com:19302 bundle-policy=max-bundle {pipeline_video_input} {pipeline_audio_input} {pipeline_save}'
            printc('\ngst-launch-1.0 ' + PIPELINE_DESC.replace('(', '\\(').replace(')', '\\)'), "FFF")
        else:
            PIPELINE_DESC = f'{pipeline_video_input} {pipeline_audio_input}'
            printc('\ngst-launch-1.0 ' + PIPELINE_DESC.replace('(', '\\(').replace(')', '\\)'), "FFF")
            
        
        if not check_plugins(needed) or error:
            sys.exit(1)

    args.server = WSS
    server = ""    
    watchURL = args.hostname
    watchURL += "?password=false&"
        
    bold_color = hex_to_ansi("FAF")
    print("\nAvailable options include --streamid, --bitrate, and --server. See --help for more options. Default video bitrate is 2500 (kbps)")
    printc(f"\n-> You can view this stream at: {bold_color}{watchURL}view={args.streamid}{server}\n", "7FF")

    args.pipeline = PIPELINE_DESC
    c = WebRTCClient(args)
    while True:
        try:
            await c.connect()
            res = await c.loop()
        except KeyboardInterrupt:
            print("Ctrl+C detected. Exiting...")
            break
        except:
            await asyncio.sleep(5)
    if c.shared_memory:
        c.shared_memory.close()
        c.shared_memory.unlink()
    sys.exit(res)
    return

if __name__ == "__main__":
    asyncio.run(main())
