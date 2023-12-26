# Copyright (c) 2021 Avanade
# Author: Thor Schueler
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#

import asyncio
import datetime
import json
import logging
import ssl
import time
import gi
import websockets

gi.require_version('Gst', '1.0')
gi.require_version('GstWebRTC', '1.0')
gi.require_version('GstSdp', '1.0')
from gi.repository import Gst, GstSdp, GstWebRTC, GObject
from typing import Dict, List
from types import SimpleNamespace
try:
    from gi.repository import GLib
except:
    pass

class WebRTCClient:
    """This class implements a WebRTC gstreamer source"""

    def __init__(self, pipeline:str, stream_id:str, server:str='wss://wss.vdo.ninja:443', stun_server:str='stun://stun4.l.google.com:19302', logging_level:int = logging.INFO, max_clients:int=5):
        """
        Initializes the class. 

        :param pipeline:    The basic streaming pipeline to use. This is a standard gstreamer pipeline, but note two things:
                            - the pipeline should contain a tee called audiotee, a tee called videotee or both. 
                            - the pipeline is started immidiately, so after the tee, you probably want to use a fakesink. As 
                              clients connect, we will add additional branches with a queue and a webrtcbin to the pipeline. 
        :type pipeline:     str

        :param stream_id:   The of the stream. Clients will need to know that to connect. 
        :type stream_id:    str

        :param server:      The signaling server. To avoid causing issues for production; default server is api.backup.obs.ninja. 
                            Streams can be view at https://backup.obs.ninja/?password=false&view={stream_id} as a result.
        :type server:       str

        :param stun_server: The STUN server usd for ICE NAT translation
        :type server:       str

        :param logging_level The logging level for the class. The logger uses htxi.modules.camera.webrtc. Defaults to logging.INFO
        :type logging_level  int

        :param max_clients  The maximum number of clients to allow concurrently. The behaior of this is a bit crazy right now. Since I have not
                            found a good way to detect when a remote clients drops off, so when this limit is reached, all clients are disconnected to free up
                            dead connections. Since gst webrtcbin supports renegotiation, active clients will simply reconnect. This needs to be
                            reworked once I figure out how to detect dropoffs to simply deny additional requests. 
        :type max_clients   int
        """
        self.__conn:websockets.connection = None
        self.__connected:bool  = False
        self.__pipe:Gst.Pipeline = None
        
        self.__stream_id:str = stream_id
        self.__server:str = server 
        self.__stun_server = stun_server
        self.__pipeline:str = pipeline
        self.__max_clients:int = max_clients
        self.__client_monitoring_period:int = 10
        self.__check_client_heartbeat:bool = True
        self.__missed_heartbeats_allowed = 3
        self.__force_client_disconnect_duration = 0
        self.__clients = {}
        self.__bitrate:int = 4000
        self.__bitrate_qos:int = self.__bitrate
        self.__buffer:int = 200
        self.__packet_loss = -1
        self.__bitrate_effective = -1
        self.__stats_cache = SimpleNamespace(** {
            'packet_loss': 0,
            'packets_sent': 0,
            'bytes_sent': 0,
            'timestamp': 0
        })
        self.__roundtrip_time = -1
        
        self.__restart:bool = False
        self.__eventloop:asyncio.BaseEventLoop = asyncio.get_event_loop()
        self.__logger:logging.Logger = logging.getLogger('htxi.modules.camera.webrtc')
        self.__logger.setLevel(logging_level)
        self.__connection_lock = asyncio.Lock()
        self.__removal_lock = asyncio.Lock()

    #
    # region public properties
    #
    @property
    def Bitrate(self) -> int:
        """
        Gets the desired encoding bit rate. Only applies if there is an encoder element with the name 'econder' such as 
        x264enc  name="encoder" bitrate={args.bitrate} speed-preset=1 tune=zerolatency qos=true
        """
        return self.__bitrate

    @Bitrate.setter
    def Bitrate(self, bitrate:int):
        """
        Sets the desired encoding bit rate. Only applies if there is an encoder element with the name 'econder' such as 
        x264enc  name="encoder" bitrate={args.bitrate} speed-preset=1 tune=zerolatency qos=true

        :param bitrate   The desired bitrate
        :type bitrate    int
        """
        self.__bitrate = bitrate
        self.__bitrate_qos = bitrate    
    
    @property
    def BufferLatency(self) -> int:
        """
        Gets the jitter buffer latency in milliseconds; default is 200ms. (gst +v1.18)
        """
        return self.__buffer

    @BufferLatency.setter
    def BufferLatency(self, buffer_latency:int):
        """
        Sets the jitter buffer latency in milliseconds; default is 200ms. (gst +v1.18)

        :param buffer_latency   The desired jitter buffer latency
        :type buffer_latency    int
        """
        self.__buffer = buffer_latency    
    
    @property
    def CheckClientHeartbeat(self) -> bool:
        """
        Gets whether we'll be checking for client heartbeat. This is recommended, but assumes that the client supports this function via responding to a ping 
        message on the data channel, such as done by vdo.ninja >= 19
        """
        return self.__check_client_heartbeat

    @CheckClientHeartbeat.setter
    def CheckClientHeartbeat(self, check:bool):
        """
        Sets whether we'll be checking for client heartbeat over data channel

        :param check    Whether or not to check heartbeat
        :type check     bool
        """
        self.__check_client_heartbeat = check

    @property
    def Clients(self) -> int:
        """ 
        Gets the number of connected clients. We really have not figured that out yet correctly, so this right now
        will show the number of active webrtcbins in the pipeline. Unfortunately, i do not know yet how to detect
        whether a client disconnects from the feed...
        """
        count = 0
        for uuid in self.__clients:
            if self.__clients[uuid]['webrtc'] is not None: 
                count += 1
        return count

    @property 
    def ClientMonitoringPeriod(self) -> int:
        """
        Gets the period with which we'll monitor for client connection health
        """
        return self.__client_monitoring_period

    @ClientMonitoringPeriod.setter
    def ClientMonitoringPeriod(self, period:int):
        """
        Sets the period with which we'll monitor for client connection health
        """
        self.__client_monitoring_period = period

    @property 
    def EffectiveBitRate(self) -> int:
        '''
        Gets the effective bit rate for the transmission in kbps
        '''
        return self.__bitrate_effective

    @property
    def ForceClientDisconnectDuration(self) -> int:
        """
        Gets the duration in seconds after which a client is forcibly disconnected. Use this for situations where heartbeats are not possible to use an occasional
        Disconnect of connected clients. Still active clients will simply reconnect while abandoned clients are removed. Set to 0 to disable forced disconnection. 

        Note that if you disable forced reconnection and do not have a client supporting the heartbeat protocol, the queue of clients might fill up quickly and there 
        is no way, other than a workload restart, to clear out that queue.... 
        """
        return self.__force_client_disconnect_duration

    @ForceClientDisconnectDuration.setter
    def ForceClientDisconnectDuration(self, val:int):
        """
        Sets the duration in seconds after which a client is forcibly disconnected. Use this for situations where heartbeats are not possible to use an occasional
        Disconnect of connected clients. Still active clients will simply reconnect while abandoned clients are removed. Set to 0 to disable forced disconnection. 

        Note that if you disable forced reconnection and do not have a client supporting the heartbeat protocol, the queue of clients might fill up quickly and there 
        is no way, other than a workload restart, to clear out that queue.... 
        """
        self.__force_client_disconnect_duration = val

    @property
    def MaxClients(self) -> int:
        """
        Gets the maximum number of clients to allow concurrently. The behaior of this is a bit crazy right now. Since I have not
        found a good way to detect when a remote clients drops off, so when this limit is reached, all clients are disconnected to free up
        dead connections. Since gst webrtcbin supports renegotiation, active clients will simply reconnect. This needs to be
        reworked once I figure out how to detect dropoffs to simply deny additional requests.
        """
        return self.__max_clients

    @MaxClients.setter
    def MaxClients(self, max_clients:int):
        """
        Sets the ceiling for concurrent connections. 
        """
        self.__max_clients = max_clients

    @property 
    def MissedHeartBeatsAllowed(self) -> int:
        """
        Gets the number of heartbeats allowed for clients to miss before being considered abandoned and disconnected.
        """
        return self.__missed_heartbeats_allowed

    @MissedHeartBeatsAllowed.setter
    def MissedHeartBeatsAllowed(self, val:int):
        """
        Gets the number of heartbeats allowed for clients to miss before being considered abandoned and disconnected.
        """
        self.__missed_heartbeats_allowed = val

    @property 
    def PacketLoss(self) -> float:
        '''
        Gets the packetloss in percent
        '''
        return self.__packet_loss * 100

    @property
    def Pipeline(self) -> str:
        """
        Gets the pipeline string
        """
        return self.__pipeline
    
    @Pipeline.setter
    def Pipeline(self, pipeline:str):
        """
        Sets the pipeline string. Setting the pipeline will reset it and lead to a disconnect of all clients
        """
        if pipeline != self.__pipeline:
            self.__logger.info(f"{datetime.datetime.now()}: Pipeline property has changed... restarting stream.")
            self.__pipeline = pipeline
            self.__remove_all_clients()
                # we do not start the pipeline here. 
                # the pipeline will automatically start when the first client 
                # tries to re-connect

    @property 
    def RoundTripTime(self) -> float:
        '''
        Gets roundtrip time in ms
        '''
        return self.__roundtrip_time

    @property 
    def StreamId(self) -> str:
        """
        Gets the stream id
        """
        return self.__stream_id

    @StreamId.setter
    def StreamId(self, stream_id:str):
        """
        Sets the stream id. This will require a reconnect and all clients will be dropped from the pipeline. However, the 
        pipeline itself will not restart. 
        """
        if stream_id != self.__stream_id:
            self.__logger.info(f"{datetime.datetime.now()}: StreamId property has changed... restarting stream.")
            self.__stream_id = stream_id
            self.__remove_all_clients()
            self.__restart = True
            self.__eventloop.create_task(self.connect())
            

    @property
    def Server(self) -> str:
        """
        Gets the signaling server
        """
        return self.__server

    @Server.setter
    def Server(self, server:str):
        '''
        Sets the signalling server. This will require a reconned and all clients will be dropped from the pipeline. However, the 
        pipeline itself will not restart. 
        '''
        if server != self.__server:
            self.__logger.info(f"{datetime.datetime.now()}: Server property has changed... restarting stream.")
            self.__server = server
            self.__remove_all_clients()
            self.__restart = True
            self.__eventloop.create_task(self.connect())
            
    @property
    def StunServer(self) -> str:
        '''
        Gets the name of the STUN server used for ICE NAT translation
        '''
        return self.__stun_server
    
    @StunServer.setter
    def StunServer(self, stun_server:str):
        '''
        Sets the STUN server used for ICE NAT translation
        '''
        if stun_server != self.__stun_server:
            self.__logger.info(f"{datetime.datetime.now()}: StunServer property has changed... restarting stream.")
            self.__stun_server = stun_server
            self.__remove_all_clients()
            self.__restart = True
            self.__eventloop.create_task(self.connect()) 
    
    #
    # endregion
    #

    #
    # region public methods
    # 
    async def connect(self): 
        """
        Connects to the signaling server. This method should be called prior to entering the message loop. 
        """ 
        self.__logger.info(f"{datetime.datetime.now()}: Initiating connection to signaling server {self.__server}.")
        if self.__conn is not None: 
            self.__connected = False
            await self.__conn.close()
            self.__conn = None
            
        sslctx = ssl.create_default_context()
        msg = json.dumps({"request":"seed","streamID":self.__stream_id})
        self.__logger.info(f"{datetime.datetime.now()}: {msg}")
        self.__logger.info(f"{datetime.datetime.now()}: Connect")
        self.__conn = await websockets.connect(self.__server, ssl=sslctx)
        async with self.__connection_lock:
            await self.__conn.send(msg)
            self.__connected = True
        self.__logger.info(f"{datetime.datetime.now()}: Seed started")
        
    async def loop(self):
        """
        Message loop listening to the signaling server and serving various peer requesteds. This loop should run until you no longer
        want to serve clients.
        """
        hasrun = False
        self.__logger.info(f"{datetime.datetime.now()}: WSS connected")
            # we do not start the pipeline here. 
            # the pipeline will automatically start when the first client tries to connect

        while not hasrun or self.__restart:
            hasrun = True
            while not self.__connected: await asyncio.sleep(0.1)
            self.__restart  = False
            try:
                ret = await self.__loop()
            except Exception as e:
                self.__logger.error(f"{datetime.datetime.now()}: Error in main loop: {e}. Restarting loop.")
                try:
                    self.__remove_all_clients()
                    await self.__conn.close()
                except Exception as e: 
                    pass
                self.__connected = False
                self.__conn = None
                self.__restart = True
                await self.connect()

        return ret
    #
    # endregion
    #


    #
    # region private methods
    #
    def __add_client(self, client_id:str):
        """
        Adds a new webrtcbin branch to the gstreamer pipeline and wires up the control structure. 

        :param client_id    The id of the remote peer
        :type client_id     str
        """

        if self.__max_clients > 0  and self.Clients >= self.__max_clients: 
            self.__logger.warning(f"{datetime.datetime.now()}: Active client threads exceed maximum allowed: {self.Clients}/{self.__max_clients}. Ignoring incoming requests until capacity is expanded or clients disconnect.")
            return

        if self.__pipe is None: self.__start_pipeline()

        client = None
        if client_id in self.__clients: client = self.__clients[client_id]
        else:
            self.__logger.error(f"{datetime.datetime.now()}: Peer with id {client_id} has not been created yet...")
            return

        if client['webrtc'] is not None:
            self.__logger.debug(f"{datetime.datetime.now()}: Found existing pipeline segment for client id. Removing....")
            self.__eventloop.call_soon_threadsafe(self.__remove_client(client_id))

        atee = self.__pipe.get_by_name('audiotee')
        vtee = self.__pipe.get_by_name('videotee')
        if vtee is None and atee is None: 
            self.__logger.error(f"{datetime.datetime.now()}: Pipeline does not contain audio or video tee. Cannot proceed adding a client.")
            raise Exception("Pipeline does not contain audio or video tee. Cannot proceed adding a client.")

        self.__logger.info(f"{datetime.datetime.now()}: Creating new pipeline segment")
        qa = None
        qv = None
        webrtc = Gst.ElementFactory.make("webrtcbin", client_id)
        webrtc.set_property('bundle-policy', GstWebRTC.WebRTCBundlePolicy.MAX_BUNDLE) 
        webrtc.set_property('stun-server', self.StunServer)
            
        try:
            webrtc.set_property('latency', self.__buffer)
            webrtc.set_property('async-handling', True)
        except:
            pass
        self.__pipe.add(webrtc)

        if vtee is not None:
            self.__logger.info(f"{datetime.datetime.now()}: Found video-tee to attach new stream")
            qv = Gst.ElementFactory.make('queue', f"v-queue-{client_id}")
            self.__pipe.add(qv)
        
        if atee is not None:
            self.__logger.info(f"{datetime.datetime.now()}: Found audio-tee to attach new stream")
            qa = Gst.ElementFactory.make('queue', f"a-queue-{client_id}")
            self.__pipe.add(qa)  

        self.__logger.info(f"{datetime.datetime.now()}: Linking new pipeline segment")
        if vtee is not None and qv is not None: 
            if not Gst.Element.link(vtee, qv): raise Exception ("Could not link vtee -> qv")
            if not Gst.Element.link(qv, webrtc): raise Exception ("Could not link qv -> webrtcbin")
            if qv is not None: qv.sync_state_with_parent()
        if atee is not None and qa is not None: 
            if not Gst.Element.link(atee, qa): raise Exception ("Could not link atee -> qa")
            if not Gst.Element.link(qa, webrtc): raise Exception ("Could not link qa -> webrtcbin")
            if qa is not None: qa.sync_state_with_parent()

        client['webrtc'] = webrtc
        client['qv'] = qv
        client['qa'] = qa
        client['created'] = datetime.datetime.now()
        
        client['encoder'] = self.__pipe.get_by_name('encoder')
                    # The element named encoder is used to manage qos where applicable. For rpi_cam sources
                    # pri_cam itself is the encoder through hardware and therefore should have a name='encoder' property. The bitrate
                    # property will be used. For libcam and v4l2 actual encoders like v4l2h264enc or vp8/9 should 
                    # be used for that purpose
        
        self.__logger.info(f"{datetime.datetime.now()}: Wiring new webrtcbin to control structure")
        webrtc.connect('on-ice-candidate', self.__send_ice_candidate_message)
        webrtc.connect('notify::ice-connection-state', self.__on_ice_connection_state)
        webrtc.connect('notify::connection-state', self.__on_connection_state)
        webrtc.connect('notify::signaling-state', self.__on_signaling_state)
        webrtc.connect('on-new-transceiver', self.__on_new_tranceiver)
        webrtc.connect('on-negotiation-needed', self.__on_negotiation_needed)
                    # This is the gstwebrtc entry point where we create the offer and so on. It
                    # will be called when the pipeline goes to PLAYING.
                    # Important: We must connect this ***after*** webrtcbin has been linked to a source via
                    # get_element.link() and before we go from NULL->READY otherwise webrtcbin
                    # will create an SDP offer with no media lines in it.       
        try:
            trans = client['webrtc'].emit("get-transceiver",0)
            if trans is not None:
                try:
                    trans.set_property("fec-type", GstWebRTC.WebRTCFECType.ULP_RED)
                    self.__logger.info(f"{datetime.datetime.now()}: FEC enabled")
                except:
                    pass
                trans.set_property("do-nack", True)
                self.__logger.info(f"{datetime.datetime.now()}: Send NACKS enabled")
        except Exception as e:
            self.__logger.info(f"{datetime.datetime.now()}: could not set transceiver properties: {e}")              
        
        self.__logger.info(f"{datetime.datetime.now()}: Set new GST branch to playing")    
        self.__pipe.set_state(Gst.State.PLAYING)
        webrtc.sync_state_with_parent()
        self.__logger.info(f"{datetime.datetime.now()}: Pipeline status: {self.__pipe.get_state(0)[1].value_nick}")
        if not client['send_channel']:
            channel = client['webrtc'].emit('create-data-channel', 'sendChannel', None)
            self.__logger.info(f"{datetime.datetime.now()}: Creating data channel for client {client_id}")
            if channel is None:
                self.__logger.warning(f"{datetime.datetime.now()}: Data channel not available")
            else:
                self.__logger.info(f"{datetime.datetime.now()}: Setting up data channel for client {client_id}")                              
                channel.connect('on-open', self.__on_data_channel_open)
                channel.connect('on-error', self.__on_data_channel_error)
                channel.connect('on-close', self.__on_data_channel_close)
                channel.connect('on-message-string', self.__on_data_channel_message)
                self.__clients[client_id]['send_channel_pending'] = channel
        
    async def __check_clients_occasionally(self):
        """
        Occasionally checks for the health of the connection to the peers and frees up connections for peers that have dropped off. 
        """
        self.__logger.debug(f"{datetime.datetime.now()}: Client connection checker task started") 
        while True:
            if self.__check_client_heartbeat is True:
                for client_id in self.__clients.copy():
                    client = self.__clients[client_id]
                    webrtcbin = self.__clients[client_id]['webrtc']
                    should_remove_client = False
                    if not client['send_channel']:
                        self.__logger.warning(f"{datetime.datetime.now()}: Data channel for client {client_id} not setup yet")
                    else:
                        if 'ping' not in client: client['ping'] = 0
                        if client['ping'] < self.__missed_heartbeats_allowed:
                            self.__logger.debug(f"{datetime.datetime.now()}: Checking status of {client_id}: {webrtcbin.get_property('connection-state').value_nick}, {webrtcbin.get_property('ice-connection-state').value_nick}, {webrtcbin.get_state(0)[1].value_nick}")
                            self.__logger.info(f"{datetime.datetime.now()}: Sending ping to {client_id}.")
                            client['ping'] += 1         
                            try:
                                client['send_channel'].emit('send-string', '{"ping":"' + str(time.time()) + '"}')
                                    #
                                    # Emit ping to peer, which is responded to in the __on_data_channel_message, so there is no
                                    # additional action necessary for this iteration. We will check the heartbeat result on the next iteration 
                                    # as seen below....
                                    #
                            except Exception as e:
                                should_remove_client = True
                                self.__logger.error(f"{datetime.datetime.now()}: Failed to send ping request for client {client_id}: {e}")
                                
                            promise = Gst.Promise.new_with_change_func(self.__on_stats, self.__clients[client_id]['webrtc'], None)
                            webrtcbin.emit("get-stats", None, promise)
                        else:
                            self.__logger.warning(f"{datetime.datetime.now()}: Peer {client_id} has failed too many heartbeats. Disconnecting peer from pipeline")
                            should_remove_client = True

                    if self.__force_client_disconnect_duration != 0 and self.__clients[client_id]['created'] + datetime.timedelta(seconds=self.__force_client_disconnect_duration) < datetime.datetime.now():
                        #
                        # This is to deal with situations where heartbeat cannot be applied (like vod.ninja < 1.19)
                        #
                        self.__logger.warning(f"{datetime.datetime.now()}: Peer {client_id} has exceeded force disconnect period. Disconnecting peer from pipeline")
                        should_remove_client = True

                    if should_remove_client is True:
                        self.__logger.info(f"{datetime.datetime.now()}: Removing client {client_id} due to data channel error or abandonment")
                        self.__remove_client(client_id)

            await asyncio.sleep(self.__client_monitoring_period)   

    def __on_get_stats(self, promise:Gst.Promise, client_id:str, __):
        """
        Callback used by webrtcbin to inform us that the stats promise has been resolved and starts are ready. 

        :param promise      Fullfilled promise contianing the stats data. 
        :type promise       Gst.Promise

        :param client_id    Id of the client bracnh for which the stats are obtained. 
        :type client_id     str 
        """
        def __field_inspector(id, value, _):
            self.__logger.debug(f"{datetime.datetime.now()}: {id} - {value}")
            return True
        
        self.__logger.debug(f"{datetime.datetime.now()}: recevied stats for {client_id}")
        reply = promise.get_reply()
        self.__logger.debug(f"{datetime.datetime.now()}: {reply.get_name_id()} - {reply.get_name()} - {reply.n_fields()} fields")
        reply.foreach(__field_inspector, None)
        val = reply.id_get_value(2849)
        if reply.n_fields() > 5:
            val = reply.get_value(reply.nth_field_name(5))
            val.foreach(__field_inspector, None)
            self.__logger.debug(f"{datetime.datetime.now()}: {reply.has_field('remote-inbound-rtp')} - {reply.nth_field_name(5)} - {val.get_name()} - {val.to_string()}")

    def __handle_sdp(self, client_id:str, msg:Dict[str, object]):
        """
        Handles the SDP communication. This method is called by loop when an SDP anwer is received.  

        :param client_id    The id of the peer
        :type client_id     str

        :param msg          The message to be processed
        :type message       Dict[str, object]
        """
        client = self.__clients[client_id]
        if not client or not client['webrtc']:
            self.__logger.info(f"{datetime.datetime.now()}: __handle_sdp: Client for {client_id} not found or invalid.")
            return        
        
        if 'sdp' in msg:
            self.__logger.info(f"{datetime.datetime.now()}: Incoming answer SDP type: {msg['type']}")
            webrtc = client['webrtc']
            assert(webrtc)
            assert(msg['type'] == 'answer')
            self.__logger.info(f"{datetime.datetime.now()}: Handle SDP")
            sdp = msg['sdp']
            self.__logger.debug(f"{datetime.datetime.now()}: Received answer: {sdp}")
            res, sdpmsg = GstSdp.SDPMessage.new()
            GstSdp.sdp_message_parse_buffer(bytes(sdp.encode()), sdpmsg)
            answer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg)
            promise = Gst.Promise.new()
            webrtc.emit('set-remote-description', answer, promise)
            promise.interrupt()          
        else: 
            self.__logger.warning(f"{datetime.datetime.now()}: Unexpected incoming message: {msg}")
            
    def __handle_candidate(self, client_id:str, msg:Dict[str, object]):
        """
        Handles the candidate negotiation  

        :param client_id    The id of the peer
        :type client_id     str

        :param msg          The message to be processed
        :type message       Dict[str, object]
        """
        
        client = self.__clients[client_id]
        if not client or not client['webrtc']:
            self.__logger.info(f"{datetime.datetime.now()}: __handle_candidate: Client for {client_id} not found or invalid.")
            return    
        
        webrtc = client['webrtc']
        assert(webrtc) 

        if 'candidate' in msg:
            self.__logger.info(f"{datetime.datetime.now()}:      Handling inbound ICE CANDIDATE; {msg['candidate']}")
            candidate = msg['candidate']
            sdpmlineindex = msg['sdpMLineIndex']
            webrtc.emit('add-ice-candidate', sdpmlineindex, candidate)  

    async def __loop(self):
        """
        The main loop that listens via socket connection to the signaling server to initiate
        peer connections.
        """
        uuid:str = None
        try:
            assert self.__conn
            self.__logger.info(f"{datetime.datetime.now()}: Start message loop")
            asyncio.get_event_loop().create_task(self.__check_clients_occasionally())
            async for message in self.__conn:
                try:
                    msg = json.loads(message)
                    self.__logger.debug(f"{datetime.datetime.now()}: Message loop received message: {msg}")

                    if 'UUID' in msg: uuid = msg['UUID'] 
                    else: 
                        continue
                    
                    if uuid not in self.__clients:
                        self.__clients[uuid] = {}
                        self.__clients[uuid]["UUID"] = uuid
                        self.__clients[uuid]["session"] = None
                        self.__clients[uuid]["send_channel"] = None
                        self.__clients[uuid]["ping"] = 0
                        self.__clients[uuid]["webrtc"] = None
                    
                    if 'session' in msg: 
                        if not self.__clients[uuid]['session']:
                            self.__clients[uuid]['session'] = msg['session']
                        elif self.__clients[uuid]['session'] != msg['session']:
                            self.__logger.warning(f"{datetime.datetime.now()}: Incoming session {msg['session']} does not match expected session {self.__clients[uuid]['session']}")                    
                    
                    if 'description' in msg:
                        self.__logger.info(f"{datetime.datetime.now()}: Received description via WSS")
                        msg = msg['description']
                        if 'type' in msg:
                            if msg['type'] == "offer": 
                                self.__logger.warning(f"{datetime.datetime.now()}: Received incoming offer. Incoming calls are not supported for this use.")
                            elif msg['type'] == "answer":
                                self.__handle_sdp(uuid, msg)
                                
                    elif 'candidates' in msg:
                        self.__logger.info(f"{datetime.datetime.now()}: ICE candidates bundle via WSS")
                        if type(msg['candidates']) is list:
                            for ice in msg['candidates']:
                                self.__handle_candidate(uuid, ice)
                            
                    elif 'candidate' in msg:
                        self.__logger.info(f"{datetime.datetime.now()}: ICE candidate single via WSS")
                        self.__handle_candidate(uuid, ice)
                            
                    elif 'request' in msg:
                        self.__logger.info(f"{datetime.datetime.now()}: Request via WSS {msg['request']}")
                        if 'offerSDP' in  msg['request']:
                            self.__logger.info(f"{datetime.datetime.now()}: Received streaming request")
                            self.__add_client(uuid)
                        else:
                            self.__logger.warning(f"{datetime.datetime.now()}: Received unknown request: {message}")
                    else:
                        self.__logger.warning(f"{datetime.datetime.now()}: Received unknown message: {message}")
                except websockets.ConnectionClosed:
                    self.__logger.warning(f"{datetime.datetime.now()}: Web sockets closed; retrying in 5s")
                    await asyncio.sleep(5)
                    continue
        except asyncio.CancelledError:
            self.__logger.info(f"{datetime.datetime.now()}: Reveived cancellation request. Exiting...")
            self.__pipe.set_state(Gst.State.NULL)
            self.__remove_all_clients()
            await self.__conn.close()
            del self.__pipe
        except Exception as e:
            self.__logger.error(f"{datetime.datetime.now()}: Unexpected error: {e}")
            raise
        finally: 
            self.__logger.info(f"{datetime.datetime.now()}: Ending WebRTC loop.")
        return 0

    def __on_connection_state(self, webrtcbin:Gst.Element, prop:object):
        """
        Handles connection state changes on the WebRtcBin. Also sets up a data channel to remote peer for heartbeat

        :param webrtcbin    The webrtcbin that detected the connection state change
        :type webrtcbin     Gst.Element (GstWebRTC)

        :param prop         The property causin the change. In this case this will be Connection-State
        :type prop          object
        """      
        val = webrtcbin.get_property(prop.name)
        uuid = webrtcbin.name
        self.__logger.info(f"{datetime.datetime.now()}: Connection status change: {val.value_nick}")
        if val==GstWebRTC.WebRTCPeerConnectionState.CONNECTED:
            self.__logger.info(f"{datetime.datetime.now()}: Peer connection active")
            self.__logger.info(f"{datetime.datetime.now()}: Setup data channel for client {uuid}")
            promise = Gst.Promise.new_with_change_func(self.__on_stats, webrtcbin, None)
            webrtcbin.emit('get-stats', None, promise)
            
            if not self.__clients[uuid]['send_channel']:
                channel:GstWebRTC.WebRTCDataChannel = webrtcbin.emit('create-data-channel', 'sendChannel', None)
                channel.connect('on-open', self.__on_data_channel_open)
                channel.connect('on-error', self.__on_data_channel_error)
                channel.connect('on-close', self.__on_data_channel_close)
                channel.connect('on-message-string', self.__on_data_channel_message)                
                self.__clients[uuid]['send_channel_pending'] = channel
            self.__clients[uuid]['ping'] = 0            
        elif val==GstWebRTC.WebRTCPeerConnectionState.DISCONNECTED or val==GstWebRTC.WebRTCPeerConnectionState.FAILED or val==GstWebRTC.WebRTCPeerConnectionState.CLOSED: 
            #
            # this won't work unless Gstreamer / LibNice support it -- which isn't the case in most versions.
            #
            self.__logger.info(f"{datetime.datetime.now()}: Peer Connection Disconnected")
            self.__remove_client(uuid)
        else: 
            self.__logger.info(f"{datetime.datetime.now()}: Peer Connection State {val}")

    def __on_data_channel_error(self, channel:GstWebRTC.WebRTCDataChannel, err:Gst.CoreError):
        """
        Handles errors reported in the data channel

        :param channel  The data channel
        :type channel   GstWebRTC.WebRTCDataChannel

        :param err      The error
        :type err       Gst.CoreError
        """
        uuid:str = None
        for cid in self.__clients:
            if self.__clients[cid]['send_channel'] == channel:
                uuid = cid
                break
        if uuid is not None: self.__logger.error(f"{datetime.datetime.now()}: Error in data channel {uuid}: {err}")

    def __on_data_channel_open(self, channel:GstWebRTC.WebRTCDataChannel):
        """
        Responds to the open event of the data channel

        :param channel  The data channel
        :type channel   GstWebRTC.WebRTCDataChannel
        """
        uuid:str = None
        for cid in self.__clients:
            if 'send_channel_pending' in self.__clients[cid] and self.__clients[cid]['send_channel_pending'] == channel:
                uuid = cid
                break
        if uuid is not None:
            self.__logger.info(f"{datetime.datetime.now()}: Data channel {uuid} opened.")
            self.__clients[uuid]['send_channel'] = channel
            del self.__clients[uuid]['send_channel_pending']

    def __on_data_channel_close(self, channel:GstWebRTC.WebRTCDataChannel):
        """
        Responds to the closing of the data channel. This is inidicative of lost connection to the peer and as such, data
        channel clost will remove the peer from the pipeline. 

        :param channel  The data channel
        :type channel   GstWebRTC.WebRTCDataChannel
        """
        uuid:str = None
        for cid in self.__clients:
            if self.__clients[cid]['send_channel'] == channel:
                uuid = cid
                break
        if uuid is not None: 
            self.__logger.info(f"{datetime.datetime.now()}: Closing data channel {uuid}")
                # we do not have hte remove the client here because the channel is closed either by handing up, which removes the 
                # client as part of handling that message or by ping failure or timeout, which will remove the client. 

    def __on_data_channel_message(self, channel:GstWebRTC.WebRTCDataChannel, msg_raw:str):
        """
        Handles messages received on the data channel

        :param channel  The data channel
        :type channel   GstWebRTC.WebRTCDataChannel

        :param msg_raw  The message received
        :type msg_raw   str

        https://github.com/steveseguin/raspberry_ninja/blob/advanced_example/nvidia_jetson/server.py
        """
        uuid:str = None
        for cid in self.__clients:
            if self.__clients[cid]['send_channel'] == channel:
                uuid = cid
                break
        if uuid is None: return 
        try:
            msg = json.loads(msg_raw)
        except:
            self.__logger.error(f"{datetime.datetime.now()}: Unexpected message format in channel - {uuid}")
            return
        
        if 'candidates' in msg:
            self.__logger.info(f"{datetime.datetime.now()}: Inbound ICE Bundle - data channel")
            for ice in msg['candidates']:
                self.__handle_candidate(uuid, ice)
        elif 'candidate' in msg:
            self.__logger.info(f"{datetime.datetime.now()}:Ibound ICE single - data channel")
            self.__handle_candidate(uuid, ice)
        if 'pong' in msg: 
            #
            # Supported in v19 of VDO.Ninja
            #
            self.__logger.info(f"{datetime.datetime.now()}: Received pong from peer on channel - {uuid}: {msg['pong']}")
            self.__clients[uuid]['ping'] = 0  
        elif 'bye' in msg: 
            #
            # supported in v19 of VDO.Ninja
            #
            self.__logger.info(f"{datetime.datetime.now()}: Peer on channel - {uuid} hung up")
            self.__eventloop.call_soon_threadsafe(self.__remove_client, uuid)
            
        elif 'description' in msg:
            self.__logger.info(f"{datetime.datetime.now()}: Incoming SDP - data channel")
            if msg['description']['type'] == "offer":
                self.__handle_sdp(uuid, msg['description'])
                
        elif 'bitrate' in msg:
            self.__logger.info(f"{datetime.datetime.now()}: incoming bitrate request {msg['bitrate']}")
            if self.__clients[uuid]['encoder'] and msg['bitrate']:
                self.__logger.info(f"{datetime.datetime.now()}: Trying to change bitrate...")
                self.__clients[uuid]['encoder'].set_property('bitrate', int(msg['bitrate'])*1000)

        else:
            self.__logger.info(f"{datetime.datetime.now()}: Unhandled message on channel {uuid}: {msg_raw[:150]}")

    def __on_ice_connection_state(self, webrtcbin:Gst.Element, prop:object):
        """
        Handles ICE connection state changes on the WebRtcBin

        :param webrtcbin    The webrtcbin that detected the connection state change
        :type webrtcbin     Gst.Element (GstWebRTC)

        :param prop         The property causin the change. In this case this will be Ice-Connection-State
        :type prop          object
        """
        self.__logger.info(f"{datetime.datetime.now()}: ICE connection status change: {webrtcbin.get_property(prop.name).value_nick}")

    def __on_negotiation_needed(self, webrtcbin:Gst.Element):
        """
        Responds to negoation request and creates the offering processes.

        :param webrtcbin    The WebRTCBin element
        :type webrtcbin     Gst.Element (WebRTCBin)
        """
        self.__logger.info(f"{datetime.datetime.now()}: On Negotiation Needed - Negotiation request. Creating offer....")
        promise = Gst.Promise.new_with_change_func(self.__on_offer_created, webrtcbin, None)
        webrtcbin.emit('create-offer', None, promise)

    def __on_new_tranceiver(self, element, trans):
        self.__logger.info(f"{datetime.datetime.now()}: On new trans")
        #trans.set_property("fec-type", GstWebRTC.WebRTCFECType.ULP_RED)
        #trans.set_property("do-nack", True)

    def __on_offer_created(self, promise:Gst.Promise, webrtcbin:Gst.Element, __): 
        """
        Creates on offer for the peer

        :param promise      The promise related to the offer
        :type promise       Gst.Promise

        :param webrtcbin    The webrtcbin element
        :type webrtcbin     Gst.Element (WebRTCBin)
        """ 
        uuid = webrtcbin.name
        self.__logger.debug(f"{datetime.datetime.now()}: On Offer Created - Start offering creation for UUID: {uuid}")
        promise.wait()
        reply = promise.get_reply()
        offer = reply.get_value('offer') 
        promise = Gst.Promise.new()
        webrtcbin.emit('set-local-description', offer, promise)
        promise.interrupt()
        self.__logger.info(f"{datetime.datetime.now()}: Sending SDP offer")
        text = offer.sdp.as_text()
        msg = {'description': {'type': 'offer', 'sdp': text}, 'UUID': uuid, 'session': self.__clients[uuid]['session'], 'streamID':self.__stream_id}
        self.__logger.debug(f"{datetime.datetime.now()}: SDP offer message: {msg}")
        self.__send_message(msg)
        self.__logger.info(f"{datetime.datetime.now()}: SDP offer message sent")

    def __on_signaling_state(self, webrtcbin:Gst.Element, prop:object):
        """
        Handles singaling state changes on the WebRtcBin

        :param webrtcbin    The webrtcbin that detected the connection state change
        :type webrtcbin     Gst.Element (GstWebRTC)

        :param prop         The property causin the change. In this case this will be Connection-State
        :type prop          object
        """
        self.__logger.info(f"{datetime.datetime.now()}: Signaling status change: {webrtcbin.get_property(prop.name).value_nick}")

    def __on_stats(self, promise, webrtcbin:Gst.Element, data):
        """
        Processes stats message from the channel

        :param promise      The promise related to the offer
        :type promise       Gst.Promise

        :param webrtcbin    The webrtcbin element
        :type webrtcbin     Gst.Element (WebRTCBin)
        """       
        uuid = webrtcbin.name
        promise.wait()
        stats = promise.get_reply()
        
        packet_loss = -1
        packets_sent = -1
        bytes_sent = -1
        timestamp = -1
        
        if stats.has_field('codec-stats-sink_0'):
            if stats.get_value('codec-stats-sink_0').has_field('ssrc'):
                ssrc = stats.get_value('codec-stats-sink_0').get_value('ssrc')
                if stats.has_field(f'rtp-remote-inbound-stream-stats_{ssrc}'):
                    rtpriss = stats.get_value(f'rtp-remote-inbound-stream-stats_{ssrc}')
                    packet_loss = rtpriss.get_value('packets-lost')
                    timestamp = rtpriss.get_value('timestamp')
                    self.__roundtrip_time = rtpriss.get_value('round-trip-time') * 1000
                    
                if stats.has_field(f'rtp-outbound-stream-stats_{ssrc}'):    
                    rtposs = stats.get_value(f'rtp-outbound-stream-stats_{ssrc}')
                    packets_sent = rtposs.get_value('packets-sent')  
                    bytes_sent = rtposs.get_value('bytes-sent')         

        if packet_loss != -1 and packets_sent != -1:
            self.__packet_loss = (packet_loss - self.__stats_cache.packet_loss)/(packets_sent - self.__stats_cache.packets_sent)             
            self.__stats_cache.packet_loss = packet_loss
            self.__stats_cache.packets_sent = packets_sent
              
        if timestamp != -1 and bytes_sent != -1:
            time_difference = (timestamp - self.__stats_cache.timestamp)
            self.__bitrate_effective = int((bytes_sent - self.__stats_cache.bytes_sent) / time_difference) * 8 
                # this is no kbps....
            self.__stats_cache.timestamp = timestamp
            self.__stats_cache.bytes_sent = bytes_sent

        self.__update_bit_rate(uuid, self.__packet_loss)
        

    def __send_ice_candidate_message(self, webrtcbin:Gst.Element, mlineindex:int, candidate:str):
        """
        Assemble and send ICE message for a particular candidate

        :param webrtcbin        The WebRTCBin element
        :type webrtcbin         Gst.Element     (WebRTCBin) 

        :param mlineindex       The candidate index
        :type mlineindex        int

        :param candidate        Candidate information
        :type candidate         str
        """
        uuid = webrtcbin.name
        self.__logger.debug(f"{datetime.datetime.now()}: SENDING ICE - UUID: {uuid}")
        icemsg = {'candidates': [{'candidate': candidate, 'sdpMLineIndex': mlineindex}], 'session':self.__clients[uuid]['session'], 'type':'local', 'UUID':uuid}
        self.__send_message(icemsg)
        self.__logger.debug(f"{datetime.datetime.now()}: SENT ICE - UUID: {uuid}, {candidate}")

    def __send_message(self, msg):
        """
        Sends a message to WSS. 
        """
        client = None
        if "UUID" in msg and msg['UUID'] in self.__clients: client = self.__clients[msg['UUID']]
    
        msg = json.dumps(msg)    
        if client and client['send_channel']:
            try:
                client['send_channel'].emit('send-string', msg)
                self.__logger.info(f"{datetime.datetime.now()}: Message was sent via datachannels: {msg[:150]}")
            except Exception as e:
                asyncio.run_coroutine_threadsafe(self.__conn.send(msg), self.__eventloop)
                self.__logger.info(f"{datetime.datetime.now()}: Message was sent via websockets after channel exception: {msg[:150]}")
        else:
            asyncio.run_coroutine_threadsafe(self.__conn.send(msg), self.__eventloop)
            self.__logger.info(f"{datetime.datetime.now()}: Message was sent via websockets: {msg[:150]}")

    def __start_pipeline(self):
        """
        Parse and start the gst pipeline. If a pipeline already exists, this pipeline will be torn down firts. 
        """
        self.__logger.info(f"{datetime.datetime.now()}: Creating pipeline: {self.__pipeline}")
        self.__logger.info(f"{datetime.datetime.now()}: Pre-roll GST pipeline")
        if self.__pipe is not None: 
            self.__logger.info(f"{datetime.datetime.now()}: Found existing pipeline. Resetting and releasing.")
            while self.__pipe.get_state(Gst.CLOCK_TIME_NONE)[1] is not Gst.State.NULL:
                self.__pipe.set_state(Gst.State.NULL)
                self.__logger.info(f"{datetime.datetime.now()}: Pipeline status: {self.__pipe.get_state(0)[1].value_nick}")
                time.sleep(0.1)
            del self.__pipe
        self.__pipe = Gst.parse_launch(self.__pipeline)

    def __remove_all_clients(self):
        """
        Removes all active clients. 
        """
        self.__logger.info(f"{datetime.datetime.now()}: Stopping pipeline")
        for client_id in self.__clients.copy(): self.__remove_client(client_id)        
        self.__logger.info(f"{datetime.datetime.now()}: Pipeline stopped")
        
    def __remove_client(self, client_id:str):
        """
        Removes a client and dismantels the webrtcbin scaffold associated with the client. 

        :param client_id    ID of the peer to remove
        :type client_id     str
        """
        
        if client_id in self.__clients:
            atee = self.__pipe.get_by_name('audiotee')
            vtee = self.__pipe.get_by_name('videotee')
            
            if atee is not None and self.__clients[client_id]['qa'] is not None: atee.unlink(self.__clients[client_id]['qa'])
            if vtee is not None and self.__clients[client_id]['qv'] is not None: vtee.unlink(self.__clients[client_id]['qv'])        
                            
            if self.__clients[client_id]['qa'] is not None: 
                if self.__clients[client_id]['webrtc'] is not None: self.__clients[client_id]['qa'].unlink(self.__clients[client_id]['webrtc'])
                self.__pipe.remove(self.__clients[client_id]['qa'])
                self.__clients[client_id]['qa'].set_state(Gst.State.NULL)
                self.__clients[client_id]['qa'] = None
            if self.__clients[client_id]['qv'] is not None:
                if self.__clients[client_id]['webrtc'] is not None: self.__clients[client_id]['qv'].unlink(self.__clients[client_id]['webrtc'])
                self.__pipe.remove(self.__clients[client_id]['qv'])
                self.__clients[client_id]['qv'].set_state(Gst.State.NULL)
                self.__clients[client_id]['qv'] = None
            if self.__clients[client_id]['webrtc'] is not None: 
                self.__pipe.remove(self.__clients[client_id]['webrtc'])
                self.__clients[client_id]['webrtc'].set_state(Gst.State.NULL)
                self.__clients[client_id]['webrtc'] = None
            del self.__clients[client_id]
            self.__logger.info(f"{datetime.datetime.now()}: Removed GST branch for client {client_id}")
        else:
            self.__logger.warning(f"{datetime.datetime.now()}: Cound not find client {client_id} for removal")
            
        if self.__pipe is not None and len(self.__clients) == 0:
            self.__logger.info(f"{datetime.datetime.now()}: All clients have disconnected, stopping pipeline")
            if self.__pipe.set_state(Gst.State.NULL) == Gst.StateChangeReturn.FAILURE: self.__logger.error(f"{datetime.datetime.now()}: Could not stop pipeline")
            while True:
                self.__logger.info(f"{datetime.datetime.now()}: Pipeline status: {self.__pipe.get_state(0)[1].value_nick}")
                if self.__pipe.get_state(0)[1] is Gst.State.NULL: break
                time.sleep(0.1)
            self.__pipe = None
            
    def __update_bit_rate(self, uuid: str, packet_loss_value: float) -> None:
        """
        Adjusts the bit rate on the channel based on reported packet loss. The effective range of the bit rate goes from
        20% of self.__bitrate to 200% of self.__bitrate. The resulting qos bitrate is stored in self.__bitrate_qos
        
        :param uuid      The ID of the pipeline
        :type uudi       str
        
        :param packet_loss_value    The observed packet loss
        :type packet_loss_value     float
        
        """  
        if self.__clients[uuid]['encoder'] is None: return
        if self.__bitrate == 0: return
        should_update = False
        if (packet_loss_value > 0.01):
            # for packet less greater than 1%, we will start reducing the bit rate by 10%, limited on the downside to 
            # 20% of the target bit rate 
            bitrate = self.__bitrate_qos * 0.9
            if bitrate < self.__bitrate * 0.2: pass
            else:
                self.__logger.info(f"{datetime.datetime.now()}: Trying to reduce bitrate from {int(self.__bitrate_qos)} to {int(bitrate)}")
                self.__bitrate_qos = bitrate
                should_update = True

        elif (packet_loss_value < 0.003):
            # for packet loss less than 0.3%, we will start increasing the bit rate by 5%, limited on the upside to 100% of the original
            # bit rate
            bitrate = self.__bitrate_qos * 1.05
            if bitrate > self.__bitrate * 2: pass
            else: 
                self.__logger.info(f"{datetime.datetime.now()}: Trying to increase bitrate from {int(self.__bitrate_qos)} to {int(bitrate)}")
                self.__bitrate_qos = bitrate
                should_update = True
                    
        if should_update:
            try:
                if self.__clients[uuid]['encoder'].find_property('bitrate') is not None: self.__clients[uuid]['encoder'].set_property('bitrate', int(self.__bitrate_qos))
                elif self.__clients[uuid]['encoder'].find_property('target-bitrate') is not None: self.__clients[uuid]['encoder'].set_property('target-bitrate', int(self.__bitrate_qos))
                elif self.__clients[uuid]['encoder'].find_property('extra-controls') is not None: 
                    extra_controls = self.__clients[uuid]['encoder'].get_property("extra-controls")
                    extra_controls_structure = Gst.Structure.new_from_string(extra_controls.to_string())
                    extra_controls_structure.set_value("video_bitrate", int(self.__bitrate_qos))
                    self.__clients[uuid]['encoder'].set_property("extra-controls", extra_controls_structure)
                else: 
                    self.__logger.warning(f"{datetime.datetime.now()}: The gst encoder element {self.__clients[uuid]['encoder'].g_type_instance.g_class.g_type.name} is not currently supported for qos.")
            except Exception as e:
                self.__logger.error(f"{datetime.datetime.now()}: {e}")        
            
    #
    # endregion
    #

async def main():
    webrtc_client:WebRTCClient = None
    stream_id = 'theRealThor'
    pipeline = 'rpicamsrc bitrate=2500000 ! video/x-h264,profile=constrained-baseline,width=1920,height=1080,framerate=(fraction)30/1,level=3.0 ! queue max-size-time=1000000000  max-size-bytes=10000000000 max-size-buffers=1000000 ! h264parse ! rtph264pay config-interval=-1 aggregate-mode=zero-latency ! application/x-rtp,media=video,encoding-name=H264,payload=96 ! tee name=videotee'
    webrtc_client = WebRTCClient(pipeline=pipeline, stream_id=stream_id)
    webrtc_client.MaxClients = 10
    webrtc_client.ClientMonitoringPeriod = 5
    webrtc_client.MissedHeartBeatsAllowed = 5
    webrtc_client.CheckClientHeartbeat = True
    webrtc_client.ForceClientDisconnectDuration = 60
    await webrtc_client.connect()
    await webrtc_client.loop()
    


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.root.setLevel(logging.INFO)
    logger = logging.getLogger("htxi.modules.camera")
    logger.setLevel(logging.INFO)
    logger.info(f'{datetime.datetime.now()}: Starting')
    Gst.init(None)
    Gst.debug_set_active(True)
    Gst.debug_set_default_threshold(0)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info(f'{datetime.datetime.now()}: Keyboard interrupt received.')
    except Exception as e:
        logger.error(f'{datetime.datetime.now()}: {e}')
    finally:
        logger.info(f'{datetime.datetime.now()}: Exiting')    