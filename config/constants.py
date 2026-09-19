from enum import Enum
busy_periods_dict = {}  # { technician_id: [ {"start": "...", "end": "..."} ] }

class StateEnum(Enum):
    CLASSIFY = "classify"
    APPOINTMENT = "appointment"
    CONSULT = "consult"
    ORDER_AFTER_SALES = "order_after_sales"
    OTHER = "other"
    
class SharedState:
    def __init__(self):
        self.value = StateEnum.CLASSIFY
