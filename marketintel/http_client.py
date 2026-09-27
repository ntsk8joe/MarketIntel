"""Bounded JSON requests to authenticated providers; redirects are never followed."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise ValueError('带凭据的供应商请求发生重定向，已停止；请核查官方接口地址')


def authenticated_json(request, limit, timeout=60):
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            blob = response.read(limit + 1)
            if len(blob) > limit: raise ValueError('供应商响应超过大小限制')
            return json.loads(blob)
    except HTTPError as exc:
        code = exc.code; exc.close()
        raise ValueError('供应商返回 HTTP ' + str(code)) from None
    except (URLError, TimeoutError, OSError):
        # Provider messages may echo credentials; do not persist request details.
        raise ValueError('供应商连接失败或响应超时；收费请求结果需到供应商控制台核查') from None
