from openant.easy.node import Node

class AntPlusDevice:
    def __init__(
        self,
        node: Node,
        device_type: int,
        device_id: int = ...,
        period: int = ...,
        rf_freq: int = ...,
        name: str = ...,
        trans_type: int = ...,
        master: bool = ...,
    ) -> None: ...
    def request_dp(self, page: int = ..., no_times: int = ...) -> None: ...
    def close_channel(self) -> None: ...
    def on_found(self) -> None: ...
    def on_data(self, data: list[int]) -> None: ...
