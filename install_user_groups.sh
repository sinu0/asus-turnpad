#!/usr/bin/env bash

source non_sudo_check.sh

source install_paths.sh

UINPUT_GID=$(getent group "uinput" | cut -d: -f3)

# https://github.com/asus-linux-drivers/asus-dialpad-driver/issues/19#issuecomment-3625958498
if [ -n "$UINPUT_GID" ] && [ "$UINPUT_GID" -ge 1000 ]; then
    echo "The group 'uinput' was not system (GID=$UINPUT_GID), removing..."
    sudo groupdel "uinput"
fi

I2C_GID=$(getent group "i2c" | cut -d: -f3)

# https://github.com/asus-linux-drivers/asus-dialpad-driver/issues/19#issuecomment-3625958498
if [ -n "$I2C_GID" ] && [ "$I2C_GID" -ge 1000 ]; then
    echo "The group 'i2c' was not system (GID=$I2C_GID), removing..."
    sudo groupdel "i2c"
fi

sudo groupadd --system "input"
sudo groupadd --system "i2c"
sudo groupadd --system "uinput"

sudo usermod -a -G "i2c,input,uinput,dialpad" $USER

if [[ $? != 0 ]]; then
    echo "Something went wrong when adding the groups to current user"
    exit 1
else
    echo "Added groups input, i2c, uinput, dialpad to current user"
fi

# workaround when is "failing" (return code is 0) adding of `input` group on BazziteOS using `usermod` or `ujust add-user-to-input-group`: https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/282
if grep -q "^input:" /etc/group; then

    # current members of group `input`
    current_members=$(grep "^input:" /etc/group | cut -d: -f4)
    
    # is current user already a member of group `input`?
    if ! echo "$current_members" | grep -qw "$USER"; then
        sudo sed -i "s/^input:\([^:]*:[^:]*:[^:]*\)/input:\1,$USER/" /etc/group
    fi
else
    # if group `input` does not exist in `/etc/group`` at all
    sudo bash -c "echo 'input:x:104:$USER' >> /etc/group"
fi

if [ "$INSTALL_IN_CONTAINER" = "1" ]; then
    echo "Container install: not loading the kernel modules uinput and i2c-dev"
else
    sudo modprobe uinput

    # check if the uinput module is successfully loaded
    if [[ $? != 0 ]]; then
        echo "uinput module cannot be loaded"
        exit 1
    else
        echo "uinput module loaded"
    fi

    sudo modprobe i2c-dev

    # check if the i2c-dev module is successfully loaded
    if [[ $? != 0 ]]; then
        echo "i2c-dev module cannot be loaded. Make sure you have installed i2c-tools package"
        exit 1
    else
        echo "i2c-dev module loaded"
    fi
fi

sudo mkdir -p /etc/modules-load.d

# every file written below is checked on its own
rule_write_failed() {
    echo "Something went wrong during adding the $1 (uinput, i2c-dev modules to auto loaded modules or the associated udev rules)"
    exit 1
}

# autodetects KERNEL and SUBSYSTEM values for /dev/uinput
read UINPUT_KERNEL UINPUT_SUBSYSTEM < <(
    udevadm info --attribute-walk --name=/dev/uinput \
    | awk -F '==' '
        /KERNEL==/ && !k { gsub(/"/,"",$2); k=$2 }
        /SUBSYSTEM==/ && !s { gsub(/"/,"",$2); s=$2 }
        k && s { print k, s; exit }
    '
)

# autodetects SUBSYSTEM for /dev/i2c-0
I2C_SUBSYSTEM=$(
    udevadm info --attribute-walk --name=/dev/i2c-0 \
    | awk -F '==' '
        /SUBSYSTEM==/ { gsub(/"/,"",$2); print $2; exit }
    '
)

echo "Udevadm detected values:"
echo "  uinput: KERNEL=$UINPUT_KERNEL  SUBSYSTEM=$UINPUT_SUBSYSTEM"
echo "  i2c:    SUBSYSTEM=$I2C_SUBSYSTEM"

# create uinput udev rule
echo 'SUBSYSTEM=="'"$UINPUT_SUBSYSTEM"'", KERNEL=="'"$UINPUT_KERNEL"'", OPTIONS+="static_node=uinput", GROUP="uinput", MODE="0660"' \
  | sudo tee "$UDEV_RULE_UINPUT" >/dev/null || rule_write_failed "uinput udev rule"

# create i2c udev rule
echo 'ACTION!="remove", KERNEL=="i2c-[0-9]*", SUBSYSTEM=="'"$I2C_SUBSYSTEM"'", TAG+="uaccess", GROUP="i2c", MODE="0660"' \
  | sudo tee "$UDEV_RULE_I2C" >/dev/null || rule_write_failed "i2c udev rule"

# load uinput and i2c-dev at boot
printf 'uinput\ni2c-dev\n' | sudo tee "$MODULES_LOAD_FILE_PATH" >/dev/null || rule_write_failed "modules-load file"

# create hidraw udev rule (hidraw node of the touchpad is the preferred way how to control
# the DialPad backlight, I2C above is used as a fallback)
#
# https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/224
# https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/315
#
# the rule has to be processed before 73-seat-late.rules so that the tag uaccess has an effect
TOUCHPAD_PHYS=$(grep -A5 -i touchpad /proc/bus/input/devices | grep -o 'Phys=i2c-[^[:space:]]*' | cut -d= -f2 | sort -u | head -n 1)

if [ -n "$TOUCHPAD_PHYS" ]; then
    echo 'ACTION!="remove", SUBSYSTEM=="hidraw", KERNELS=="'"$TOUCHPAD_PHYS"'", TAG+="uaccess", GROUP="i2c", MODE="0660"' \
      | sudo tee "$UDEV_RULE_HIDRAW" >/dev/null || rule_write_failed "hidraw udev rule"
else
    echo "Touchpad Phys not detected, skipping adding hidraw udev rule"
fi

# the screen of a 3Dconnexion SpacePilot Pro (USB 046d:c629): uaccess lets the logged-in user open it,
# SYSTEMD_USER_WANTS makes the user's service manager start $SCREEN_SERVICE while the device is present
# (the unit itself is placed by install_screen.sh); packaging/70-asus-turnpad.rules has the same rule
echo 'ACTION!="remove", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="046d", ATTR{idProduct}=="c629", TAG+="uaccess", TAG+="systemd", ENV{SYSTEMD_USER_WANTS}+="'"$SCREEN_SERVICE"'"' \
  | sudo tee "$UDEV_RULE_SCREEN" >/dev/null || rule_write_failed "screen udev rule"

sudo udevadm control --reload-rules && sudo udevadm trigger --sysname-match=uinput && sudo udevadm trigger --attr-match=subsystem=i2c-dev && sudo udevadm trigger --subsystem-match=hidraw && sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=046d --attr-match=idProduct=c629

if [[ $? != 0 ]]; then
    echo "Something went wrong when reloading or triggering uinput udev rules"
else
    echo "Udev rules reloaded and triggered"
fi