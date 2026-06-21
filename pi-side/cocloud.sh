#!/usr/bin/env bash
set -e

cd /opt/coglet-pi
sudo alsactl --file /opt/coglet-pi/alsa-cloud.state restore
source /opt/coglet-pi/env-exports.sh
python coglet-cloud.py
