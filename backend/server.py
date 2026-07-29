"""Compatibility entry point for the MH-Dowsample local server."""

from __future__ import annotations

UNCONFIGURED_LABEL = "Chưa chọn - ứng dụng sẽ hỏi khi bắt đầu tải"

if __name__ == "__main__":
    import server_engine as _base
    import server_hardening as _implementation
    import direct_audio as _direct_audio  # noqa: F401
    import server_streaming as _streaming  # noqa: F401
    import cancel_control as _cancel_control  # noqa: F401

    _base.UNCONFIGURED_DOWNLOAD_ROOT_LABEL = UNCONFIGURED_LABEL
    _implementation.main()
else:
    import sys
    import server_engine as _base
    import server_hardening as _implementation
    import direct_audio as _direct_audio  # noqa: F401
    import server_streaming as _streaming  # noqa: F401
    import cancel_control as _cancel_control  # noqa: F401

    _base.UNCONFIGURED_DOWNLOAD_ROOT_LABEL = UNCONFIGURED_LABEL
    sys.modules[__name__] = _implementation
