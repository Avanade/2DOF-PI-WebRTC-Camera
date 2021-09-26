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
# pylint: disable=C0103
# pylint: disable=R0913

from typing import Dict
from .controller import Controller
from .PCA9685 import PCA9685
from .ArduCamPTZ import ArduCamPTZ
from .schemas import controller_schema


class ControllerFactory(object):

    @classmethod
    def create(cls, props:Dict[int, object]) -> Controller:
        """ 
        Creates an appropriate Controller object from a dictionary of properties
        :param data: The dictionary containing the controller properities. Must adhere to Controller.ControllerSchema
        :type data: Dict[int, any]
        :return: A new controller object
        """

        if "type" not in props.keys():
            raise Exception("Controller property misses type attribute")
        
        instance = None
        if props["type"] == "PCA9865": instance = PCA9685.from_dict(props)
        elif props["type"] == "Arducam PTZ": instance = ArduCamPTZ.from_dict(props)
        else:
            raise Exception(f"Unknown controller type: {props['type']}")

        return instance
        
