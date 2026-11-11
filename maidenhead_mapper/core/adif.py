"""ADIF log reader — placeholder for Phase 2.

Contract the import algorithm will rely on:

    read(path) -> Iterable[dict]

Each dict is one QSO with lower-case ADIF field names as keys
(e.g. "call", "gridsquare", "band", "mode", "rst_sent", "rst_rcvd",
"qso_date", "time_on", "my_gridsquare") and string values.

The user's existing summarization project will be adapted to this
contract (or the contract to it) when it is added.
"""


def read(path):
    raise NotImplementedError("ADIF import arrives in Phase 2")
