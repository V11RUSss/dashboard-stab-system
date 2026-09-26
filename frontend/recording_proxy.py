"""Same-origin streaming proxy; no PKL loading inside the browser or Dash worker."""
import urllib.error
import urllib.request
from flask import Response, request, stream_with_context


def register_recording_routes(server):
    @server.route('/api/recordings/<path:operation>', methods=['GET', 'POST'])
    def recording_api(operation):
        if request.method == 'POST' and request.headers.get('X-Requested-With') != 'recordings':
            return {'error': 'Недопустимый запрос'}, 403
        url = 'http://127.0.0.1:8766/' + operation
        if request.query_string:
            url += '?' + request.query_string.decode('ascii')
        upstream = urllib.request.Request(url, data=request.get_data() if request.method == 'POST' else None,
            headers={'Content-Type': 'application/json'}, method=request.method)
        try:
            result = urllib.request.urlopen(upstream, timeout=125)
        except urllib.error.HTTPError as exc:
            return Response(exc.read(), status=exc.code, content_type='application/json')
        except OSError:
            return {'error': 'Сервис записей недоступен. Проверьте соединение с Backend.'}, 503
        def chunks():
            try:
                while chunk := result.read(1024*1024):
                    yield chunk
            finally:
                result.close()
        headers = {k: v for k, v in result.headers.items() if k.lower() in ('content-type', 'content-length', 'content-disposition')}
        headers['Cache-Control'] = 'no-store'
        return Response(stream_with_context(chunks()), headers=headers)
