from typing import Optional

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
    def sync_status(cls, sns: list[str]):
        return api_request(
            url='/device/remote/status',
            method=ApiMethod.POST,
            json={'sns': sns},
        )

    @classmethod
    def sync_config(cls, sns: list[str]):
        """批量拉取厂商最新配置状态并落本地快照（契约 §4.2 / C4）。"""
        return api_request(
            url='/device/remote/config',
            method=ApiMethod.POST,
            json={'sns': sns},
        )

    @classmethod
    def start_recording(cls, device_code: str, audio_id: Optional[str] = None):
        return api_request(
            url=f'/device/{device_code}/recording/start',
            method=ApiMethod.POST,
            json={'audio_id': audio_id},
        )

    @classmethod
    def stop_recording(cls, device_code: str):
        return api_request(
            url=f'/device/{device_code}/recording/stop', method=ApiMethod.POST
        )

    @classmethod
    def get_command_log(cls, device_code: str, message_id: str):
        return api_request(
            url=f'/device/{device_code}/command/{message_id}',
            method=ApiMethod.GET,
        )

    @classmethod
    def list_control_logs(cls, query: dict):
        """设备控制指令日志（含厂商 msg_id 与受理结果）。"""
        return api_request(
            url='/device/control/list', method=ApiMethod.GET, params=query
        )

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
