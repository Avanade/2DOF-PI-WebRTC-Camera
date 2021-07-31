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

import os
import sys
import asyncio
from six.moves import input
import threading
import time
import datetime
import json

from azure.iot.device.aio import IoTHubModuleClient
from azure.iot.device import MethodRequest
from device import Device
from command import CommandProcessor

async def main():

    async def command_handler(request: MethodRequest):
        # Define behavior for handling commands
        try:
            if (module_client is not None):
                if debug: print(f'{datetime.datetime.now()}: [INFO] Received command request from IoT Central: {request.name}, {request.payload}')
                if request.name in CommandProcessor.Commands():
                    await CommandProcessor.Commands()[request.name](module_client, request)
                else:
                    raise ValueError('Unknown command', request.name)
        except Exception as e:
            print(f"{datetime.datetime.now()}: [ERROR] Exception during command listener: {e}")

    async def get_twin():
        twin = await module_client.get_twin()
        print(f'{datetime.datetime.now()}: [INFO] Received module twin from IoTC: {twin}')
        twin_update_handler(twin['desired'])

    async def send_telemetry():
        # Define behavior for sending telemetry
        while True:
            try:
                telemetry = device.Telemetry
                payload = json.dumps(telemetry)
                if debug: print(f'{datetime.datetime.now()}: [INFO] Device telemetry: {payload}')
                await module_client.send_message(payload)  
            except Exception as e:
                print(f'{datetime.datetime.now()}: [ERROR] Exception during sending metrics: {e}')
            finally:
                await asyncio.sleep(sampleRateInSeconds)       

    def twin_update_handler(patch):
        nonlocal debug, sampleRateInSeconds
        if debug: print(f'{datetime.datetime.now()}: [INFO] Received twin update from IoT Central: {patch}')
        if 'period' in patch: sampleRateInSeconds = patch['period']
        if 'debug' in patch: debug = patch['debug']

    async def report_properties():
        propertiesToUpdate = device.Info
        if debug: print(f'{datetime.datetime.now()}: [INFO] Device properties sent to IoT Central: {propertiesToUpdate}')
        await module_client.patch_twin_reported_properties(propertiesToUpdate)
        if debug: print(f'{datetime.datetime.now()}: [INFO] Device properties updated.') 

    try:
        if not sys.version >= "3.5.3":
            print(f'{datetime.datetime.now()}:[ERROR] This module requires python 3.5.3+. Current version of Python: {sys.version}.')
            raise Exception( 'This module requires python 3.5.3+. Current version of Python: %s' % sys.version )
        print(f'{datetime.datetime.now()}: [INFO] IoT Hub Client for Python')

        sampleRateInSeconds = 10        # can be updated through the module twin in IoTC
        debug = True                    # can be updated through the module twin in IoTC
        device = Device()

        # The client object is used to interact with your Azure IoT hub.
        module_client = IoTHubModuleClient.create_from_edge_environment()
        module_client.on_method_request_received = command_handler
        module_client.on_twin_desired_properties_patch_received = twin_update_handler
        await module_client.connect()

        # start send telemetry and receive commands
        await asyncio.gather(
            get_twin(),
            report_properties(),
            send_telemetry()
        )     

    except Exception as e:
        print(f'{datetime.datetime.now()}: [ERROR] Unexpected error {e}')
        raise
    finally:
        await module_client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())