#!/usr/bin/env bash

source non_sudo_check.sh

send_test_activation() {
    local number=$1
    local addr=$2

    sudo /usr/sbin/i2ctransfer -f -y "$number" w13@0x"$addr" \
        0x05 0x00 0x3d 0x03 0x06 0x00 0x07 0x00 0x0d 0x14 0x03 0x01 0xad \
    && sudo /usr/sbin/i2ctransfer -f -y "$number" w13@0x"$addr" \
        0x05 0x00 0x3d 0x03 0x06 0x00 0x07 0x00 0x0d 0x14 0x03 0x60 0xad
}

send_test_deactivation() {
    local number=$1
    local addr=$2

    sudo /usr/sbin/i2ctransfer -f -y "$number" w13@0x"$addr" \
    0x05 0x00 0x3d 0x03 0x06 0x00 0x07 0x00 0x0d 0x14 0x03 0x00 0xad
}

# i2cdetect is /usr/sbin/i2cdetect and some distributions do not add sbin to $PATH (https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/154)
# https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/218
if command -v /usr/sbin/i2cdetect >/dev/null 2>&1 && command -v /usr/sbin/i2ctransfer >/dev/null 2>&1; then

    INTERFACES=$(grep -A5 -i touchpad /proc/bus/input/devices | grep -o 'i2c-[0-9]\+' | sort -u)

    if [ -z "$INTERFACES" ]; then
        # Touchpads on other buses (PS/2, USB) have no DialPad, but the
        # driver can emulate one.
        echo "No i2c interface of a touchpad can be found, so no DialPad either."
    fi

    TOUCHPAD_WITH_DIALPAD_DETECTED=

    for INDEX in $INTERFACES; do
        echo -n "Testing interface $INDEX: "

        NUMBER=$(echo -n $INDEX | cut -d'-' -f2)

        second_try_addr=

        # OFF test 0x15 
        if send_test_deactivation "$NUMBER" 15 >/dev/null 2>&1; then
            first_try_addr=15

            # OFF test 0x38
            if send_test_deactivation "$NUMBER" 38 >/dev/null 2>&1; then
                second_try_addr=38
            fi
        # OFF test 0x38
        elif send_test_deactivation "$NUMBER" 38 >/dev/null 2>&1; then
            first_try_addr=38
        else
            echo "failed"
            continue
        fi

        if [ "$INTERACTIVE" != "1" ]; then
            # Unattended: the first address that accepts the command wins.
            DEVICE_ADDRESS=$first_try_addr
            TOUCHPAD_WITH_DIALPAD_DETECTED=true
            echo "responds at 0x$first_try_addr"
            break
        fi

        send_test_activation "$NUMBER" "$first_try_addr"
        echo
        if ask "Did DialPad light up? (0x$first_try_addr)" n; then
            DEVICE_ADDRESS=$first_try_addr
            TOUCHPAD_WITH_DIALPAD_DETECTED=true
            send_test_deactivation "$NUMBER" "$first_try_addr"
            break
        fi

        if [ -n "$second_try_addr" ]; then

            send_test_activation "$NUMBER" "$second_try_addr"
            if ask "Did DialPad light up? (0x$second_try_addr)" n; then
                DEVICE_ADDRESS=$second_try_addr
                TOUCHPAD_WITH_DIALPAD_DETECTED=true
                send_test_deactivation "$NUMBER" "$second_try_addr"
                break
            fi
        fi

        echo "failed"
    done

    if [ -z "$TOUCHPAD_WITH_DIALPAD_DETECTED" ]; then
        echo "The detection was not successful. Touchpad with DialPad not found. Check whether your touchpad has integrated DialPad (e.g. on product websites) and in case it has then create an issue here https://github.com/sinu0/asus-turnpad/issues (see PORTING.md)."
        echo "A touchpad without a DialPad works too: after the install switch on Driver > Touchpad mode in the Turnpad settings app."

        # Unattended installs continue: the touchpad may still work once
        # calibrated, and the driver picks the I2C address itself.
        if ! ask "Do you want try to continue anyway?" y; then
            exit 1
        fi
    fi
else
    echo "The i2cdetect or i2ctransfer tool not found to proceed initial test whether any i2c device react like DialPad"
fi