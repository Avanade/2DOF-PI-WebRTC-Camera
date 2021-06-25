#!/bin/bash

echo "[INFO]"
echo "[INFO] Updating base system"
echo "[INFO]"
sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get remove libtag1-vanilla -y
sudo apt-get -y --force-yes install libomxil-bellagio-dev libfreetype6-dev libmp3lame-dev libx264-dev fonts-freefont-ttf libasound2-dev meson \
    g++ git scons libqt4-dev libqt4-sql-sqlite libportmidi-dev libopusfile-dev libshout3-dev libtag1-dev libprotobuf-dev protobuf-compiler \
    libusb-1.0 libusb-1.0-0-dev libfftw3-dev libmad0-dev libchromaprint-dev librubberband-dev libsqlite3-dev  \
    libid3tag0-dev libflac-dev libupower-glib-dev liblilv-dev libjack-dev libjack0 portaudio19-dev libmp4v2-dev cmake  \
    build-essential autotools-dev automake autoconf checkinstall libtool autopoint libxml2-dev zlib1g-dev libglib2.0-dev \
    pkg-config bison flex python3 python3-dev python3-pip python-gi-dev python3-gi python-gst-1.0 wget tar gtk-doc-tools libgudev-1.0-dev libcdparanoia-dev \
    libtheora-dev libvisual-0.4-dev iso-codes libraw1394-dev libiec61883-dev libavc1394-dev \
    libv4l-dev libcaca-dev libspeex-dev libpng-dev libjpeg-dev libdv4-dev libwavpack-dev libsoup2.4-dev libbz2-dev \
    libcdaudio-dev libdc1394-22-dev ladspa-sdk libass-dev libcurl4-gnutls-dev libdca-dev libdvdnav-dev \
    libexempi-dev libexif-dev libfaad-dev libgme-dev libgsm1-dev libiptcdata0-dev libkate-dev libmimic-dev libmms-dev \
    libmodplug-dev libmpcdec-dev libofa0-dev libopus-dev librtmp-dev libsndfile1-dev libsoundtouch-dev libspandsp-dev \
    libxvidcore-dev libvpx-dev libzvbi-dev liba52-0.7.4-dev libcdio-dev libdvdread-dev libmpeg2-4-dev libopencore-amrnb-dev libopencore-amrwb-dev \
    libsidplay1-dev libtwolame-dev yasm libgirepository1.0-dev freeglut3 weston wayland-protocols pulseaudio libpulse-dev libssl-dev \
    ccache curl libcap-dev libdrm-dev libegl1-mesa-dev libgl1-mesa-dev libgles2-mesa-dev libgmp-dev libgsl0-dev \
    libmpg123-dev libogg-dev liborc-0.4-dev libpango1.0-dev libvorbis-dev libwebp-dev unzip libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev
sudo apt autoremove -y
