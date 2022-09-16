FROM docker.io/library/debian:experimental
ARG PASSWORD=ruhroh

RUN apt update && \
  apt install -y ssh python3 less sudo curl && \
  ( echo "root:$PASSWORD" | chpasswd ) && \
  ( echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config ) && \
  mkdir -p /run/sshd
CMD [ "/usr/sbin/sshd", "-D", "-e" ]
