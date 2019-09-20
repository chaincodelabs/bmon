import pprint
import datetime
import os

from flask import Flask, request, jsonify
from .db import Host


app = Flask(__name__)


@app.route('/v0/report', methods=['POST'])
def report():
    content = request.json
    content = _deserialize_body(content)
    pprint.pprint(content)
    return jsonify({'status': 'ok'})


@app.route('/', methods=['GET'])
def home():
    return 'sup'


def _deserialize_body(content: dict):
    if 'date' in content:
        content['date'] = datetime.datetime.strptime(
            content['date'], "%Y-%m-%dT%H:%M:%SZ")

    return content


def main():
    app.run(
        debug=True,
        port=os.environ.get('BMON_WEB_PORT', 8080),
        host='0.0.0.0')


if __name__ == '__main__':
    main()
