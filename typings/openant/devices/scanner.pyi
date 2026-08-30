from openant.devices.common import AntPlusDevice
from openant.easy.node import Node

class Scanner(AntPlusDevice):
    def __init__(
        self,
        node: Node,
        device_id: int = ...,
        device_type: int = ...,
        period: int = ...,
        trans_type: int = ...,
    ) -> None: ...
    def _on_data(self, data: list[int]) -> None: ...

