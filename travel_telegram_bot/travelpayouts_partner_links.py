from __future__ import annotations

import json
from dataclasses import dataclass

from http_utils import safe_http_post


class TravelpayoutsPartnerLinkError(RuntimeError):
    pass


@dataclass(slots=True)
class TravelpayoutsPartnerLinksConfig:
    api_key: str
    marker: int | None = None
    trs: int | None = None
    shorten: bool = False


class TravelpayoutsPartnerLinksClient:
    def __init__(self, config: TravelpayoutsPartnerLinksConfig) -> None:
        self._config = config

    @property
    def enabled(self) -> bool:
        return bool(self._config.api_key and self._config.marker and self._config.trs)

    def convert(self, url: str, *, sub_id: str | None = None) -> str:
        if not self.enabled or not url:
            return url

        payload: dict[str, object] = {
            "trs": int(self._config.trs or 0),
            "marker": int(self._config.marker or 0),
            "shorten": bool(self._config.shorten),
            "links": [{"url": url}],
        }
        if sub_id:
            payload["links"] = [{"url": url, "sub_id": sub_id[:64]}]

        try:
            raw = safe_http_post(
                "https://api.travelpayouts.com/links/v1/create",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-api-token": self._config.api_key,
                },
                max_retries=2,
                timeout=20,
            )
            raw_str = raw.decode("utf-8", errors="replace")
        except Exception as exc:
            raise TravelpayoutsPartnerLinkError(str(exc)) from exc

        try:
            data = json.loads(raw_str)
            result = data.get("result") or {}
            links = result.get("links") or []
            first = links[0] if links else {}
            partner_url = str(first.get("partner_url") or "").strip()
        except Exception as exc:
            raise TravelpayoutsPartnerLinkError("invalid partner links response") from exc

        return partner_url or url
