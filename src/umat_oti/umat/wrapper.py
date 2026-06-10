from __future__ import annotations

from umat_oti.core.model import UmatInterface


def wrapper_contract(interface: UmatInterface) -> dict[str, object]:
    return {
        "entry": interface.entry_name,
        "independent_variables": ["DSTRAN(1:NTENS)"],
        "dependent_variables": ["STRESS(1:NTENS)"],
        "real_outputs": ["STRESS", "STATEV", "DDSDDE"],
    }
