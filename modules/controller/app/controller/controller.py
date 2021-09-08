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
"""
    Abstract base class defining an I2C servo controller
"""
import logging
import time
import math
import json
from typing import Dict
from jsonschema import validate
from abc import ABC, abstractmethod, abstractclassmethod, abstractstaticmethod, abstractproperty
from .servo import Servo
from .servo_attributes import ServoAttributes
from .schemas import controller_schema as schema

logger = logging.getLogger('controller')

class Controller(ABC):
    """Abstract controller class."""

    @abstractmethod
    def __init__(self, address: int = 0x0, i2c = None, 
                 frequency: int = 26500000, resolution: int = 4096,
                 servo_frequency: int = 50, **kwargs):
        """__init__

        Initialize the controller.

        :param address: The hardware address of the board. Generally 0x40 unless there is more
                        than one board.
        :type address: integer

        :param i2c: I2C driver object. Generally should be None to self obtain.
        :type i2c: Adafruit_GPIO.I2C

        :param frequency: The boards oscillating frequency. Will be around 25MHz, but will
                          slightly vary by board.
        :type frequency: integer

        :param resolution: The pulse interval resolution. It is 12 bit. You should not have
                           to change that.
        :type resolution: integer

        :param servo_frequency: The pulse frequency for the attached servos. All servos on the
                                board share the same frequency.
        :type servo_frequency: integer

        :param kwargs: additional arguments
        :type kwards: point to object array

        """
        self._servos = {}
        self._servo_frequency = servo_frequency
        self._frequency = frequency
        self._resolution = resolution
        self._address = address
        self._device = None
        self._warn_on_on_off = True

        logger.info("Created abstract controller on address %d" % address)

    @classmethod
    def from_json_file(cls, json_file:str) -> object:
        """from_json_file
        Generates PCA9685 from json file
        :param json_file: name of the file containing the json data. Must adhere to Controller.ControllerSchema
        :type json_file: str
        """
        with open(json_file) as file:
            data = json.load(file)
            validate(data, schema)
        instance = cls.from_dict(data)
        return instance

    @classmethod
    def from_json(cls, json_string:str) -> object:
        """from_json
        Generates PCA9685 from json data
        :param json_string: String containing the json data. Must adhere to Controller.ControllerSchema
        :type json_string: str
        """
        data = json.loads(json_string)
        validate(data, schema)
        instance = cls.from_dict(data)
        return instance

    @classmethod
    @abstractmethod
    def from_dict(cls, data:Dict[str, object]) -> object:
        """from_dict
        Generates PCA9685 from dictionary
        :param data: The dictionary containing the servo data. Must adhere to Controller.ControllerSchema
        :type data: dictionary
        """
        pass
        return None

    @classmethod
    @abstractmethod
    def software_reset(cls, i2c=None, **kwargs):
        """Sends a software reset (SWRST) command to all servo drivers on the bus."""
        pass

    @property
    def address(self) -> int:
        """Gets the board address.

        :return: The board address.
        :rtype: int
        """
        return self._address

    @property
    def frequency(self) -> int:
        """Gets the servo frequency configured for the board.

        :return: The servo pulse frequency.
        :rtype: int
        """
        return self._servo_frequency

    @property
    def resolution(self) -> int:
        """Gets the pulse resolution for the board.

        :return: The servo pulse resolution.
        :rtype: int
        """
        return self._resolution

    @property
    def servos(self) -> Dict[str, object]:
        """Gets the collection of servos on the board.

        :return: a list of servos registerd on the board.
        :rtype: Collection of Servo
        """
        return self._servos

    @abstractmethod
    def add_servo(self, channel: int, attributes: ServoAttributes = None, move_to_neutral: bool = True):
        """add_servo
        Adds a servo definition for a given channel.

        :param channel: The channel on which the servo is operating.
        :type channel: integer

        :param attributes: The servo attribute (min/max/neutral pulses and angles).
        :type attributes: ServoAttributes

        :param move_to_neutral: Move the servo to the neutral position if True
        :type move_to_neutral: bool

        """
        pass

    def get_servo(self, channel: int) -> Servo:
        """get_servo
        Gets the servo on channel.

        :param channel: The channel for which to obtain the servo state. Between 0 and 15.
        :type channel: integer

        :rtype (Servo) -The servo on that channel.

        """
        if channel < 0 or channel > 15:
            raise ValueError('Channel must be between 0 and 15')

        if channel not in self._servos:
            raise KeyError('There is no servo registered on channel %d' % channel)
        
        servo = self._servos[channel]
        return servo

    @abstractmethod
    def set_servo_pulse(self, channel: int, pulse: float):
        """set_servo_pulse
        Sets the servo on channel to a certain pulse width.

        :param channel: The channel for which to obtain the servo state. Between 0 and 15.
        :type channel: integer

        :param pulse: The pulse length to set.
        :type pulse: float

        """
        pass

    @abstractmethod
    def set_servo_angle(self, channel: int, angle: float):
        """set_servo_angle
        Sets the servo on channel to a certain angle.

        :param channel: The channel for which to obtain the servo state. Between 0 and 15.
        :type channel: integer

        :param angle: The angle to set. The finest resolution is about 0.5 degrees.
        :type pulse: angle

        """
        pass

    @abstractmethod
    def set_pwm_freq(self, servo_frequency: int):
        """set_pwm_freq
        Set the PWM frequency to the provided value in hertz.

        :param servo_frequency: The frequency of the servo pulse.
        :type servo_frequency: integer

        """
        pass

    @abstractmethod
    def set_off(self, channel: int, tf: bool = True):
        """set_off
        Toggles channel off state. Note that turning the channel totally of will not retain the servo angle
        against manual manipulation

        :param channel: The channel on which to operate.
        :type channel: int
        
        :param tf: Set to True to turn the channel off, False to turn the channel back to PWM
        :type tf: bool
        
        """
        pass

    @abstractmethod
    def set_pwm(self, channel: int, on_ticks: int, off_ticks: int):
        """set_pwm
        Sets a single PWM channel pulse.

        :param channel: The channel for which to obtain the servo state. Between 0 and 15.
        :type channel: integer

        :param on_ticks: Number of ticks into a period at which to switch the pulse on.
        :type pulse: integer

        :param off_ticks: Number of ticks into a period at which to switch the pulse off.
        :type pulse: integer

        """
        pass

    @abstractmethod
    def set_all_pwm(self, on_ticks: int, off_ticks: int):
        """set_pwm
        Sets all PWM channel pulse.

        :param on_ticks: Number of ticks into a period at which to switch the pulse on.
        :type pulse: integer

        :param off_ticks: Number of ticks into a period at which to switch the pulse off.
        :type pulse: integer

        """
        pass
