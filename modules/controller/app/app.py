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
import asyncio
import datetime
import json
import logging

from jsonmerge import merge
from typing import Dict, List
from azure.iot.device.aio import IoTHubModuleClient
from azure.iot.device import MethodRequest
from cam import Cam
from command import CommandProcessor

logging.basicConfig(level=logging.DEBUG)

async def main():

    def boot_environment(env:Dict[str,object], test:bool = False):
        nonlocal last_action
        cams: Dict[str,Cam] = Cam.boot_from_dict(env)
        for name in Cam.get_names():
            cam:Cam = Cam.get(name)
            if test: cam.test()
            cam.turn_off()
        last_action = datetime.datetime.now()

    async def command_handler(request: MethodRequest):
        nonlocal last_action
        # Define behavior for handling commands
        try:
            if (module_client is not None):
                logger.info(f'{datetime.datetime.now()}: Received command request from IoT Central: {request.name}, {request.payload}')
                if request.name in CommandProcessor.Commands():
                    last_action = datetime.datetime.now()
                    await CommandProcessor.Commands()[request.name](module_client, request)
                    await send_telemetry(one_time_only=True)
                else:
                    raise ValueError('Unknown command', request.name)
        except Exception as e:
            logger.error(f"{datetime.datetime.now()}: Exception during command listener: {e}")

    async def get_twin():
        twin = await module_client.get_twin()
        logger.info(f'{datetime.datetime.now()}: Received module twin from IoTC: {twin}')
        await twin_update_handler(twin['desired'])

    async def powerdown_watcher():
        while True:
            if (datetime.datetime.now() - last_action).seconds > powerdown: 
                for name in Cam.get_names():
                    cam:Cam = Cam.get(name)
                    if not cam._turnedOff:
                        cam.turn_off()
                        logger.info(f'{datetime.datetime.now()}: Device channels powered down for {name}')
            await asyncio.sleep(1)

    async def send_telemetry(one_time_only:bool=False):
        # Define behavior for sending telemetry
        while True:
            try:
                names:List[str] = Cam.get_names()
                if len(names) > 0:
                    cam:Cam = Cam.get(names[0])
                    telemetry = {
                        'base': cam.position[0],
                        'elevation': cam.position[1]
                    }
                    payload = json.dumps(telemetry)
                    logger.info(f'{datetime.datetime.now()}: Device telemetry: {payload}')
                    await module_client.send_message(payload)  
            except Exception as e:
                logger.error(f'{datetime.datetime.now()}: Exception during sending metrics: {e}')
            finally:
                if one_time_only: 
                    break
                else:
                    await asyncio.sleep(sampleRateInSeconds)       

    async def twin_update_handler(patch):
        nonlocal sampleRateInSeconds, powerdown, env, last_action
        last_action = datetime.datetime.now()
        logger.info(f'{datetime.datetime.now()}: Received twin update from IoT Central: {patch}')
        if 'period' in patch: sampleRateInSeconds = patch['period']
        if 'logging_level' in patch: 
            logging.root.setLevel(patch['logging_level'])
            logger.setLevel(patch['logging_level'])
        if 'az_logging_level' in patch: logging.getLogger("azure.iot.device").setLevel(patch['az_logging_level'])
        if 'powerdown' in patch: powerdown = patch['powerdown']
        if 'environment' in patch: 
            env = merge(env, patch['environment'])
            boot_environment(env, True if 'started' not in env else False)
            env['started'] = True
        if 'position' in patch:
            position = patch['position']
            names:List[str] = Cam.get_names()
            if len(names) > 0:
                cam:Cam = Cam.get(names[0])
                if env['started']:
                    x = cam.position[0] if 'base' not in position else position['base']
                    y = cam.position[1] if 'elevation' not in position else position['elevation']
                    logger.info(f"Attempting to move camera {cam.name} to {x}:{y}")
                    cam.turn_on()
                    cam.position = (x, y)
                    cam.turn_off()
                    logger.info(f"Move of camera {cam.name} to {x}:{y} complete.")
                    if (module_client is not None):
                        await module_client.patch_twin_reported_properties({
                            'position': {
                                'base': cam.position[0],
                                'elevation': cam.position[1]
                            }
                        })   
        #
        # Send a property update back to IoTC to confirm the new property settings, if this is not done, the properties will only 
        # be considered desired, not reports and therefore not show up in Dashboard, etc. 
        #
        del patch['$version']
        await module_client.patch_twin_reported_properties(patch)
        logger.info(f'{datetime.datetime.now()}: Desired properties confirmed to reported properties.')  

    try:
        logging.root.setLevel(logging.INFO)
        logging.getLogger("azure.iot.device").setLevel(logging.WARNING)
        logger = logging.getLogger("htxi.module.controller")
        logger.setLevel(logging.INFO)
        
        if not sys.version >= "3.5.3":
            logger.error(f'{datetime.datetime.now()}: This module requires python 3.5.3+. Current version of Python: {sys.version}.')
            raise Exception( 'This module requires python 3.5.3+. Current version of Python: %s' % sys.version )
        logger.info(f'{datetime.datetime.now()}: IoT Hub Client for Python')

        powerdown = 60                  # can be updated through the module twin in IoTC
        sampleRateInSeconds = 10        # can be updated through the module twin in IoTC
        env = {}                        # cam environment definition. can be updated through the module twin in IoTC
        last_action = datetime.datetime.now()

        # The client object is used to interact with your Azure IoT hub.
        module_client = IoTHubModuleClient.create_from_edge_environment()
        module_client.on_method_request_received = command_handler
        module_client.on_twin_desired_properties_patch_received = twin_update_handler
        await module_client.connect()
        await get_twin()

        # start send telemetry and receive commands
        await asyncio.gather(
            send_telemetry(),
            powerdown_watcher()
        )     

        for name in Cam.get_names():
            cam = Cam.get(name)
            cam.reset()
            cam.turn_off()
        Cam.controller_reset()

    except Exception as e:
        logger.error(f'{datetime.datetime.now()}: Unexpected error {e}')
        raise
    finally:
        await module_client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())