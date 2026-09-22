from config.enums import ApiMethod
from utils.request import api_request


class DeviceApi:
    """设备管理后台接口。"""

    @classmethod
    def list_devices(cls, query: dict):
        return api_request(
            url='/device/list', method=ApiMethod.GET, params=query
        )

    @classmethod
    def get_device(cls, device_id: int):
        return api_request(
            url=f'/device/detail/{device_id}', method=ApiMethod.GET
        )

    @classmethod
    def add_device(cls, payload: dict):
        return api_request(url='/device', method=ApiMethod.POST, json=payload)

    @classmethod
    def update_device(cls, payload: dict):
        return api_request(url='/device', method=ApiMethod.PUT, json=payload)

    @classmethod
    def delete_devices(cls, device_ids: str):
        return api_request(url=f'/device/{device_ids}', method=ApiMethod.DELETE)

    @classmethod
    def list_callbacks(cls, query: dict):
        return api_request(
            url='/device/callback/list', method=ApiMethod.GET, params=query
        )

    @classmethod
    def get_callback(cls, callback_id: int):
        return api_request(
            url=f'/device/callback/{callback_id}', method=ApiMethod.GET
        )

    @classmethod
    def delete_callbacks(cls, callback_ids: str):
        return api_request(
            url=f'/device/callback/{callback_ids}', method=ApiMethod.DELETE
        )
