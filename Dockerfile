FROM steamcmd/steamcmd:ubuntu-24

RUN dpkg --add-architecture i386 \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl \
        tar \
        dbus \
        python3 \
        xvfb \
        xauth \
        gosu \
        xkb-data \
        libgl1 \
        libgl1:i386 \
        libgl1-mesa-dri \
        libgl1-mesa-dri:i386 \
        libvulkan1 \
        libvulkan1:i386 \
        mesa-vulkan-drivers \
        mesa-vulkan-drivers:i386 \
        libsdl2-2.0-0 \
        libsdl2-2.0-0:i386 \
        libxrender1 \
        libxrender1:i386 \
        libxrandr2 \
        libxrandr2:i386 \
        libxinerama1 \
        libxinerama1:i386 \
        libxi6 \
        libxi6:i386 \
        libxcursor1 \
        libxcursor1:i386 \
        libfreetype6 \
        libfreetype6:i386 \
    && rm -f /etc/machine-id \
    && dbus-uuidgen --ensure=/etc/machine-id \
    && curl -fsSL -o /tmp/proton.tar.gz \
        "https://github.com/GloriousEggroll/proton-ge-custom/releases/download/GE-Proton10-34/GE-Proton10-34.tar.gz" \
    && mkdir -p /tmp/proton \
    && tar -xzf /tmp/proton.tar.gz -C /tmp/proton --strip-components=1 \
    && cp -a /tmp/proton/. /usr/local/bin/ \
    && chmod +x /usr/local/bin/proton \
    && rm -rf /tmp/proton /tmp/proton.tar.gz /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /data

COPY config/ /opt/acevo/config/
COPY --chmod=755 scripts/ /opt/acevo/scripts/

ENTRYPOINT ["/opt/acevo/scripts/start.sh"]
