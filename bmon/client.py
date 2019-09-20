import json
import urllib.request
import logging
import zmq

logger = logging.getLogger(__name__)


context = zmq.Context()


class EventSender:

    def _prepare_event_for_send(self, event) -> dict:
        sanitized = dict(event.__dict__)
        del sanitized['_sa_instance_state']
        sanitized['_event_name'] = event.__tablename__
        sanitized['_auth_token'] = self.auth_token
        return sanitized


class ZMQSender(EventSender):

    def __init__(self, host_auth_token, zmq_socket_addr):
        self.auth_token = host_auth_token
        self.socket = context.socket(zmq.REQ)
        self.socket.connect(zmq_socket_addr)

    def send(self, event):
        self.socket.send(self._prepare_event_for_send(event).encode())


class HTTPSender(EventSender):
    """Unused ATM."""
    # TODO handle network errors during send; queue resends.

    def __init__(self, host_auth_token, server_url='localhost:5000'):
        self.auth_token = host_auth_token
        self.server_url = server_url

    def receive(self, event):
        logger.debug("Received event %s, sending to server", event)
        prepared = self._prepare_event_for_send(event)
        req = urllib.request.Request(
            f'http://{self.server_url}/v0/report',
            data=json.dumps(prepared).encode('utf8'),
            headers={'content-type': 'application/json'})
        resp = urllib.request.urlopen(req)

        return resp.read().decode('utf8')
