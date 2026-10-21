#!/bin/sh
# xrdp session start: always XFCE, with a clean D-Bus/runtime environment.
unset DBUS_SESSION_BUS_ADDRESS
unset XDG_RUNTIME_DIR
if [ -r /etc/default/locale ]; then
  . /etc/default/locale
  export LANG LANGUAGE
fi
exec /usr/bin/startxfce4
