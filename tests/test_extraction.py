import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from fakturama_automation.extraction.extractor import extract_order
from fakturama_automation.workflow.errors import ExtractionError

VALID_PAYLOAD = {
    "order_date": "2026-07-14",
    "external_reference": "WEB-2026-0714-A17",
    "debtor": {
        "company": "Northstar Office GmbH",
        "first_name": "Marta",
        "last_name": "Klein",
        "alias": "NORTHSTAR-BERLIN",
        "billing_address": {
            "street": "Friedrichstrasse 88",
            "zip": "10117",
            "city": "Berlin",
            "country": "Germany",
        },
        "delivery_address": {
            "street": "Beusselstrasse 44",
            "zip": "10553",
            "city": "Berlin",
            "country": "Germany",
        },
    },
    "payment": {"method": "Bank Transfer", "status": "PAID", "payment_date": "2026-07-18"},
    "items": [
        {
            "sku": "CHR-ERG-01",
            "description": "Ergonomic Desk Chair",
            "quantity": 2,
            "unit_net_price": 250,
            "vat_percentage": 19,
            "discount_percentage": 10,
            "source_total": 450.00,
        }
    ],
    "total_net": 450.00,
    "vat_total": 85.50,
    "total": 535.50,
}


@pytest.fixture
def sample_image(tmp_path: Path) -> Path:
    path = tmp_path / "order.png"
    Image.new("RGB", (10, 10)).save(path)
    return path


def _mock_client(content: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.output_text = content
    client.responses.create.return_value = response
    return client


def test_extract_order_parses_valid_response(sample_image):
    client = _mock_client(json.dumps(VALID_PAYLOAD))
    order = extract_order(sample_image, client=client)
    assert order.external_reference == "WEB-2026-0714-A17"
    assert len(order.items) == 1
    assert order.items[0].sku == "CHR-ERG-01"


def test_extract_order_rejects_invalid_json(sample_image):
    client = _mock_client("not json")
    with pytest.raises(ExtractionError):
        extract_order(sample_image, client=client)


def test_extract_order_rejects_schema_mismatch(sample_image):
    bad_payload = dict(VALID_PAYLOAD)
    del bad_payload["items"]
    client = _mock_client(json.dumps(bad_payload))
    with pytest.raises(ExtractionError):
        extract_order(sample_image, client=client)


def test_extract_order_missing_file():
    client = _mock_client(json.dumps(VALID_PAYLOAD))
    with pytest.raises(ExtractionError):
        extract_order("does_not_exist.png", client=client)
