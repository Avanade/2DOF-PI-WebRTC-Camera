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

import sys
import os
import asyncio
import datetime
import json
import logging
import argparse
import gi

from jsonmerge import merge
from azure.iot.device.aio import IoTHubModuleClient
from azure.iot.device import MethodRequest
from types import SimpleNamespace
from typing import Dict, List
from server import WebRTCClient
from device_watcher import Device_Watcher
from packaging import version
from usb.core import find as finddev, show_devices

gi.require_version('Gst', '1.0')
gi.require_version('GstWebRTC', '1.0')
gi.require_version('GstSdp', '1.0')
from gi.repository import Gst, GstSdp, GstWebRTC

logging.basicConfig(level=logging.DEBUG)
logging.root.setLevel(logging.INFO)
logger = logging.getLogger("htxi.modules.camera")
logger.setLevel(logging.INFO)

test_video_pipeline = '''
tee name=videotee ! queue ! fakesink
videotestsrc ! {custom} videoconvert ! queue ! vp8enc deadline=1 ! rtpvp8pay ! queue ! application/x-rtp,media=video,encoding-name=VP8,payload=97 ! videotee.
tee name=audiotee ! queue ! fakesink
audiotestsrc is-live=true wave=red-noise volume=0.2 ! queue ! opusenc ! rtpopuspay ! queue leaky=1 ! application/x-rtp,media=audio,encoding-name=OPUS,payload=96 ! audiotee.
''' # test source with video and audio.

rpi_cam_pipeline = '''
rpicamsrc name="encoder" {source_params} ! video/x-h264,profile=constrained-baseline,width={width},height={height},framerate=(fraction){fps}/1,level=3.0 ! {custom} queue max-size-time=1000000000  max-size-bytes=10000000000 max-size-buffers=1000000 ! h264parse ! rtph264pay config-interval=-1 aggregate-mode=zero-latency ! 
queue ! application/x-rtp,media=video,encoding-name=H264,payload=96 ! tee name=videotee
''' # raspberry pi camera needed; audio source removed to perserve simplicity.

lib_cam_pipeline = '''
libcamerasrc {source_params} ! video/x-raw,width=(int){width},height=(int){height},format=(string)RGB,framerate=(fraction){fps}/1 ! {custom} v4l2convert ! videorate ! 
video/x-raw,format=I420 ! v4l2h264enc extra-controls="controls,video_bitrate={bitrate};" qos=true name="encoder" ! video/x-h264,level=(string)4 ! 
queue max-size-time=1000000000  max-size-bytes=10000000000 max-size-buffers=1000000 ! h264parse  ! rtph264pay config-interval=-1 aggregate-mode=zero-latency ! 
application/x-rtp,media=video,encoding-name=H264,payload=96 ! tee name=videotee
''' # raspberry pi camera needed; audio source removed to perserve simplicity.

v4l_pipeline = '''
tee name=videotee ! queue ! fakesink
v4l2src {source_params} ! {caps} ! {custom} videoconvert ! queue ! vp8enc deadline=1 target-bitrate={bitrate} ! rtpvp8pay ! queue ! application/x-rtp,media=video,encoding-name=VP8,payload=97 ! videotee.
''' # usb camera needed; audio source removed to perserve simplicity.

async def main(settings:SimpleNamespace):
    """
    The main method handling the module functionality and optionally the IoTHub and IoTCentral connectivity.

    :param settings     Settings dictionary. Passed as namespace to make access to the members easier
    :type settings      SimpleNamespace. 
    """

    def build_gst_pipeline() -> bool:
        """
        Build the gst pipeline based ont he settings. 
        """
        if settings.cam_source == 'test': settings.gst_pipeline = test_video_pipeline.format(custom=settings.custom_pipeline)
        elif settings.cam_source == 'rpi_cam': 
            settings.gst_pipeline = rpi_cam_pipeline.format(source_params=settings.cam_source_params, custom=settings.custom_pipeline, width=settings.width, height=settings.height, fps=settings.fps)
            settings.gst_pipeline = settings.gst_pipeline.format(bitrate=settings.bit_rate)
        elif settings.cam_source == 'v4l2src': 
            settings.gst_pipeline = v4l_pipeline.format(caps=settings.caps, source_params=settings.cam_source_params, custom=settings.custom_pipeline, bitrate=settings.bit_rate)
            settings.gst_pipeline = settings.gst_pipeline.format(width=settings.width, height=settings.height, fps=settings.fps)
        elif settings.cam_source == 'libcamera': settings.gst_pipeline = lib_cam_pipeline.format(source_params=settings.cam_source_params, custom=settings.custom_pipeline, width=settings.width, height=settings.height, fps=settings.fps, bitrate=settings.bit_rate)
        else:
            return False
        return True

    def on_device_does_not_exist():
        logger.warning(f'{datetime.datetime.now()}: Camera device either missing or malfunctioned. Exiting container to attempt recovery')
        sys.exit(500)

    async def command_handler(request: MethodRequest):
        # Define behavior for handling commands
        try:
            if (module_client is not None):
                logger.info(f'{datetime.datetime.now()}: Received command request from IoT Central: {request.name}, {request.payload}')
                #if request.name in CommandProcessor.Commands():
                #    await CommandProcessor.Commands()[request.name](module_client, request)
                #else:
                #    raise ValueError('Unknown command', request.name)
        except Exception as e:
            logger.error(f"{datetime.datetime.now()}: Exception during command listener: {e}")

    async def get_twin():
        """
        Get the device twin from IoTCentral or IoTHub
        """
        twin = await module_client.get_twin()
        logger.info(f'{datetime.datetime.now()}: Received module twin from IoTC: {twin}')
        await twin_update_handler(twin['desired'])

    async def send_telemetry():
        """
        Continously send telemetry to IoTCentral/IoT Hub
        """
        while True:
            try:
                telemetry = {
                    "connected_clients": webrtc_client.Clients,
                    "stream_url_t": f"{settings.peer_url_base}/?password=false&view={settings.stream_id}"
                }
                if(webrtc_client.EffectiveBitRate != -1): telemetry["bit_rate"] = webrtc_client.EffectiveBitRate
                if(webrtc_client.RoundTripTime != -1): telemetry["roundtrip_time"] = webrtc_client.RoundTripTime
                if(webrtc_client.PacketLoss != -100): telemetry["packet_loss"] = webrtc_client.PacketLoss

                payload = json.dumps(telemetry)
                logger.info(f'{datetime.datetime.now()}: Device telemetry: {payload}')
                await module_client.send_message(payload)  
            except Exception as e:
                logger.error(f'{datetime.datetime.now()}: Exception during sending metrics: {e}')
            finally:
                await asyncio.sleep(sampleRateInSeconds)       

    async def twin_update_handler(patch:Dict[str, object]):
        """
        Handle Device Twin updates received from IoTCentral or IoT Hub.

        :param path     The property patch
        :type path      Dictionary
        """
        nonlocal sampleRateInSeconds, settings
        logger.info(f'{datetime.datetime.now()}: Received twin update from IoT Central: {patch}')
        if 'period' in patch: sampleRateInSeconds = patch['period']
        if 'az_logging_level' in patch: logging.getLogger('azure.iot.device').setLevel(patch['az_logging_level'])
        if 'max_clients' in patch: settings = SimpleNamespace(** merge(settings.__dict__, {'max_clients': patch['max_clients']}))
        if 'monitoring_period' in patch: settings = SimpleNamespace(** merge(settings.__dict__, {'monitoring_period': patch['monitoring_period']}))
        if 'missed_heartbeats_allowed' in patch: settings = SimpleNamespace(** merge(settings.__dict__, {'missed_heartbeats_allowed': patch['missed_heartbeats_allowed']}))
        if 'check_client_heartbeat' in patch: settings = SimpleNamespace(** merge(settings.__dict__, {'check_client_heartbeat': patch['check_client_heartbeat']}))
        if 'force_client_disconnect_duration' in patch: settings = SimpleNamespace(** merge(settings.__dict__, {'force_client_disconnect_duration': patch['force_client_disconnect_duration']}))
        if 'cam_vendor_id' in patch: settings = SimpleNamespace(** merge(settings.__dict__, {'cam_vendor_id': int(patch['cam_vendor_id'], 16)}))
        if 'cam_product_id' in patch: settings = SimpleNamespace(** merge(settings.__dict__, {'cam_product_id': int(patch['cam_product_id'], 16)}))
        if 'settings' in patch: 
            settings = SimpleNamespace(** merge(settings.__dict__, patch['settings']))
            build_gst_pipeline()
        if webrtc_client is not None:
            webrtc_client.Pipeline = settings.gst_pipeline
            webrtc_client.StreamId = settings.stream_id      
            webrtc_client.Server = settings.server
            webrtc_client.StunServer = settings.stun_server
            webrtc_client.ClientMonitoringPeriod = settings.monitoring_period
            webrtc_client.MaxClients = settings.max_clients
            webrtc_client.MissedHeartBeatsAllowed = settings.missed_heartbeats_allowed
            webrtc_client.CheckClientHeartbeat = settings.check_client_heartbeat
            webrtc_client.ForceClientDisconnectDuration = settings.force_client_disconnect_duration
            webrtc_client.Bitrate = settings.bit_rate
        if 'logging_level' in patch: 
            logging.root.setLevel(patch['logging_level'])
            logger.setLevel(patch['logging_level'])
        #
        # Send a property update back to IoTC to confirm the new property settings, if this is not done, the properties will only 
        # be considered desired, not reports and therefore not show up in Dashboard, etc. 
        #
        del patch['$version']
        patch['stream_url'] = f'{settings.peer_url_base}/?password=false&view={settings.stream_id}'
        await module_client.patch_twin_reported_properties(patch)
        logger.info(f'{datetime.datetime.now()}: Desired properties confirmed to reported properties.') 

    main_loop = asyncio.get_running_loop()
    module_client:IoTHubModuleClient = None
    webrtc_client:WebRTCClient = None
    try:        
        if not version.parse(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}") >= version.parse("3.5.3"):
            logger.error(f'{datetime.datetime.now()}: This module requires python 3.5.3+. Current version of Python: {sys.version}.')
            raise Exception( 'This module requires python 3.5.3+. Current version of Python: %s' % sys.version )

        if settings.useAZIoT:
            logging.getLogger("azure.iot.device").setLevel(logging.WARNING)
            logger.info(f'{datetime.datetime.now()}: IoT Hub Client for Python')
            sampleRateInSeconds = 10        # can be updated through the module twin in IoTC

            # The client object is used to interact with your Azure IoT hub.
            module_client = IoTHubModuleClient.create_from_edge_environment()
            module_client.on_method_request_received = command_handler
            module_client.on_twin_desired_properties_patch_received = twin_update_handler
            await module_client.connect()
            await get_twin()

        if not build_gst_pipeline():
            logger.error(f'{datetime.datetime.now()}: Invalid camera source: {settings.cam_source}. Use test|rpi_cam|libcamera|v4l2src')
            sys.exit(1)

        if settings.stream_id is None:
            logger.error(f'{datetime.datetime.now()}: Missing stream id. Either set using the STREAM_ID envrionment variable, stream_id from IoTHub or the --streamid command line switch.')
            sys.exit(1)

        if settings.cam_source == 'v4l2src' and settings.cam_vendor_id != 0 and settings.cam_product_id != 0:
            logger.info(f"{datetime.datetime.now()}: Reset Camera USB Device {hex(settings.cam_vendor_id)}:{hex(settings.cam_product_id)} in case it has crashed and re-registered on /dev/videoX.")
            dev = finddev(idVendor=settings.cam_vendor_id, idProduct=settings.cam_product_id)
            if dev is not None:
                dev.reset()
            else:
                logger.warning(f"{datetime.datetime.now()}: USB Device {hex(settings.cam_vendor_id)}:{hex(settings.cam_product_id)} was not found. Skipping reset attempt. Check the values in device twin or environment.")

            dev_path = settings.cam_source_params.split('=')[1]
            logger.info(f"{datetime.datetime.now()}: Start background watcher to detect camera on {dev_path}.")
            watcher = Device_Watcher(path=dev_path, error_callback=on_device_does_not_exist, blocking=False, logging_level=logger.getEffectiveLevel())
            watcher.start()


        logger.info(f'{datetime.datetime.now()}: Booting up using settings: {settings}')
        webrtc_client = WebRTCClient(pipeline=settings.gst_pipeline, stream_id=settings.stream_id, server=settings.server, stun_server=settings.stun_server)
        webrtc_client.MaxClients = settings.max_clients
        webrtc_client.ClientMonitoringPeriod = settings.monitoring_period
        webrtc_client.MissedHeartBeatsAllowed = settings.missed_heartbeats_allowed
        webrtc_client.CheckClientHeartbeat = settings.check_client_heartbeat
        webrtc_client.ForceClientDisconnectDuration = settings.force_client_disconnect_duration
        webrtc_client.Bitrate = settings.bit_rate
        webrtc_client.BufferLatency = 200
        await webrtc_client.connect()

        # start send telemetry and receive commands        
        if settings.useAZIoT: 
            asyncio.create_task(send_telemetry())

        await webrtc_client.loop()

    except asyncio.CancelledError:
        logger.info(f'{datetime.datetime.now()}: Main task was cancelled. Cleaning up.')
    except Exception as e:
        logger.error(f'{datetime.datetime.now()}: Unexpected error: {type(e)}: {e}')
        raise
    finally:
        if settings.useAZIoT and module_client is not None: await module_client.disconnect()

def check_plugins():
    needed = ["opus", "vpx", "nice", "webrtc", "dtls", "srtp", "rtp", "rtpmanager", "videotestsrc", "audiotestsrc"]
    missing = list(filter(lambda p: Gst.Registry.get().find_plugin(p) is None, needed))
    if len(missing):
        logger.error(f"{datetime.datetime.now()}: Missing gstreamer plugins: {missing}")
        return False
    return True

if __name__ == "__main__":
    logger.info(f'{datetime.datetime.now()}: Starting')
    Gst.init(None)
    if not check_plugins(): sys.exit(1)

    settings = SimpleNamespace(** {
        'stream_id': os.environ.get('STREAM_ID','htxi1234'),
        'peer_url_base': os.environ.get('PEER_URL_BASE', 'https://vdo.ninja'),
        'server': os.environ.get('SERVER', 'wss://wss.vdo.ninja:443'),
        'stun_server': os.environ.get('STUN_SERVER', 'stun://stun4.l.google.com:19302'),
        'cam_source': os.environ.get('CAM_SOURCE', 'test'),
        'cam_source_params': os.environ.get('CAM_SOURCE_PARAMS', 'device=/dev/video0'),
        'custom_pipeline': os.environ.get('CUSTOM_PIPELINE', ''),
        'bit_rate': int(os.environ.get('BIT_RATE', '4000')),
        'fps': int(os.environ.get('FPS', '10')),
        'width': int(os.environ.get('WIDTH', '1280')),
        'height': int(os.environ.get('HEIGHT', '720')),
        'caps': os.environ.get('CAPS', 'video/x-raw,width={width},height={height},framerate={fps}/1'),
        'useAZIoT': True if os.environ.get('USE_AZ_IOT', 'FALSE') == 'TRUE' else False,
        'gst_pipeline': '',
        'max_clients': 5,
        'monitoring_period': 30,
        'missed_heartbeats_allowed': 3,
        'check_client_heartbeat': True,
        'force_client_disconnect_duration': 0, 
        'cam_vendor_id': int('0x0', 16),
        'cam_product_id': int('0x0', 16)
    })

    parser = argparse.ArgumentParser()
    parser.add_argument('--streamid', help='Stream ID of the peer to connect to')
    parser.add_argument('--server', help='Handshake server to use, eg: "wss://backupapi.obs.ninja:443"')
    parser.add_argument('--stun_server', help='STUN server used for ICE NAT translation, eg: "stun://stun4.l.google.com:19302"')
    parser.add_argument('--peer_url_base', help='The base url for watching the stream, eg: "https://vdo.ninja"')    
    parser.add_argument('--cam_source', help='Video source type. Use test|rpi_cam|v4l2src.')
    parser.add_argument('--cam_source_params', help='Optional parameters for cam source. Currently only used for v4l2src to determine video device, i.e. device=/dev/video0')
    parser.add_argument('--custom_pipeline', help='Injection of custom gst pipeline plugins. Supplied plugins are injected between source and ! videoconvert ! to allow manipulation of the video source. If supplied, the value must terminate with a bang (!) Example: videoflip method=vertical-flip !')
    parser.add_argument('--width', help='video frame width. Must be supported by camera')
    parser.add_argument('--height', help='video frame height. Must be supported by camera')
    parser.add_argument('--framerate', help='video frame rate. Must be supported by camera')
    parser.add_argument('--caps', help='caps for gstreamer pipleine. Default is video/x-raw,width={width},height={height},framerate={fps}/1')
    parser.add_argument('--useAZIoT', help='Use Azure IoTEdge to manage from IoTHub. This requires that this program is deployed via an appropriate container. Connection to the Azure IoTHub is managed through the EdgeHub workload, so no connection parameters are required.', action='store_true')
    args = parser.parse_args()

    if(args.streamid is not None): settings.stream_id = args.streamid
    if(args.server is not None): settings.server = args.server
    if(args.peer_url_base is not None): settings.peer_url_base = args.peer_url_base
    if(args.stun_server is not None): settings.stun_server = args.stun_server
    if(args.cam_source is not None): settings.cam_source = args.cam_source
    if(args.cam_source_params is not None): settings.cam_source_params = args.cam_source_params
    if(args.custom_pipeline is not None): settings.custom_pipeline = args.custom_pipeline
    if(args.framerate is not None): settings.fps = int(args.framerate)
    if(args.width is not None): settings.width = int(args.width)
    if(args.height is not None): settings.height = int(args.height)
    if(args.useAZIoT): settings.useAZIoT = True

    try:
        asyncio.run(main(settings))
    except KeyboardInterrupt:
        logger.info(f'{datetime.datetime.now()}: Keyboard interrupt received.')
    finally:
        logger.info(f'{datetime.datetime.now()}: Exiting')
