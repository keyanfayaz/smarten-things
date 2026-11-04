"""Scoped local write client; no credential in argv, environment or logs."""
import http.client
import json
import re
from urllib.parse import urlsplit

from things_previews import ChangeError, normalize_change, valid_id
from things_service import load_token


class ChangeClientError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__("Change request unavailable; redacted reason available")


class ChangeClient:
    def __init__(self, token_file, base_url="http://127.0.0.1:8766"):
        url = urlsplit(base_url)
        if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost'} or url.username or url.password
                or url.path not in {'', '/'} or url.query or url.fragment):
            raise ValueError("Change API requires exact local loopback")
        self.url, self.token_file = url, token_file

    def _request(self, method, path, body=None):
        token = load_token(self.token_file)
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8') if body is not None else None
        connection = http.client.HTTPConnection(self.url.hostname, self.url.port, timeout=75)
        try:
            headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
            if data is not None: headers['Content-Type'] = 'application/json'
            connection.request(method, path, data, headers)
            response = connection.getresponse(); raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024: raise ValueError
            result = json.loads(raw)
            if response.status != 200:
                reason = result.get('reason') or result.get('error') if type(result) is dict else None
                if type(reason) is not str or re.fullmatch(r'[a-z_]{1,80}', reason) is None:
                    reason = 'change_api_unavailable'
                raise ChangeClientError(reason)
            if type(result) is not dict: raise ValueError
            return result
        except ChangeClientError:
            raise
        except Exception:
            raise ChangeClientError('change_api_unavailable') from None
        finally:
            connection.close()

    def preview(self, change):
        return self._request('POST', '/v1/change-previews', normalize_change(change))

    def apply_request(self, arguments):
        if type(arguments) is not dict or set(arguments) != {'proposal_id', 'payload_digest', 'change'} or not valid_id(arguments['proposal_id']):
            raise ChangeError('invalid_apply_request')
        return self._request('POST', '/v1/change-previews/' + arguments['proposal_id'] + '/apply',
                             {'payload_digest': arguments['payload_digest'], 'change': normalize_change(arguments['change'])})

    def status(self, arguments):
        if type(arguments) is not dict or set(arguments) != {'proposal_id'} or not valid_id(arguments['proposal_id']):
            raise ChangeError('invalid_status_request')
        return self._request('GET', '/v1/change-previews/' + arguments['proposal_id'])
