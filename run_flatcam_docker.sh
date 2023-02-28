#!/bin/bash -x
docker run -it --rm --name flatcam1 \
    -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
    -v $XAUTHORITY:/home/user/.Xauthority \
    -v /home/$USER/user:/home/user \
    -v /home/$USER/SCHEMA:/home/user/SCHEMA \
    -e DISPLAY="$DISPLAY" \
    -e RUNAS="$USER" \
    --device /dev/dri/ \
    --network=host \
    -e QT_DEBUG_PLUGINS=0 \
    flatcam
