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
import time
import datetime
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class Device_Watcher(object):

    def __init__(self, path:str, error_callback, blocking:bool=False, logging_level=logging.INFO):
        self.__path = path
        self.__callback = error_callback
        self.__exit = False
        self.__started = False
        self.__blocking = blocking
        self.__observer = Observer()
        self.__handler = FileSystemEventHandler()
        self.__handler.on_deleted = self.__on_deleted
        self.__logger:logging.Logger = logging.getLogger('htxi.modules.camera.watcher')
        self.__logger.setLevel(logging_level)

    def start(self):
        if not os.path.exists(self.__path):
            self.__on_deleted(None)
        else:
            self.__logger.info(f'{datetime.datetime.now()}: Starting file system watcher for {self.__path}')
            self.__start()
            if self.__blocking:
                try:
                    while not self.__exit:
                        self.__logger.info(f'{datetime.datetime.now()}: Checking file system for {self.__path}')
                        time.sleep(10)
                finally:
                    self.__stop()

    def stop(self):
        if self.__blocking: 
            self.__exit = True
        else: 
            self.__stop()

    def __on_deleted(self, event):
        self.__logger.info(f'{datetime.datetime.now()}: Deletion detected on {self.__path}')
        self.__callback()
        self.stop()

    def __schedule(self):
        self.__observer.schedule(self.__handler, self.__path, recursive=False)

    def __start(self):
        self.__schedule()
        self.__observer.start()
        self.__started = True

    def __stop(self):
        if self.__started:
            self.__observer.stop()
            if self.__blocking: 
                self.__observer.join()