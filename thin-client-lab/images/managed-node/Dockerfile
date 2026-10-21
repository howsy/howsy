# A "freshly installed" Ubuntu 24.04 machine: systemd, SSH and python, nothing else.
# Stands in for a session-host VM or an endpoint straight after PXE/autoinstall.
# Everything beyond this is applied by Ansible, exactly as on real hardware.
FROM ubuntu:24.04
ENV container=docker
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      systemd systemd-sysv dbus openssh-server python3 sudo iproute2 iputils-ping ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && systemctl mask getty.target systemd-firstboot.service systemd-udevd.service \
                   systemd-modules-load.service sys-kernel-config.mount \
 && systemctl enable ssh \
 && mkdir -p /root/.ssh && chmod 700 /root/.ssh \
 && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
# The lab's public key is mounted at /etc/lab/authorized_key. Copy it with strict
# permissions at boot (sshd rejects the loose modes of Windows/macOS bind mounts).
COPY lab-ssh-key.service /etc/systemd/system/lab-ssh-key.service
RUN systemctl enable lab-ssh-key.service
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
