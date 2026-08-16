import base64
import json
import mimetypes
import os
from pathlib import Path

from openai import OpenAI
from pydantic import ValidationError as PydanticValidationError

from fakturama_automation.models.order import OrderData
from fakturama_automation.utils.logging import log
from fakturama_automation.workflow.errors import ExtractionError

MODEL = "gpt-5.4-mini"

EXTRACTION_PROMPT = """You are reading a single order document image (purchase order, sales
order, or similar). Extract ONLY the data visible on the document. Do not invent values, and
leave a field null/omitted if it is not present on the document.

Return the following fields, matching this exact JSON schema:

- order_date (YYYY-MM-DD)
- external_reference (the order/reference number on the document)
- debtor: company, first_name, last_name, alias (nullable),
  billing_address: {street, zip, city, country, email (nullable), telephone (nullable)},
  delivery_address: {street, zip, city, country, email (nullable), telephone (nullable)}

  The customer's contact email and telephone are frequently printed in a
  separate "Contact" section rather than physically inside the address
  block. If the document shows one email/phone for the customer as a
  whole (not a separate one per address), fill it into both
  billing_address.email/telephone and delivery_address.email/telephone --
  do not leave these null just because they weren't printed on the same
  lines as the street/city.
- payment: method, status ("PAID" or "UNPAID"), payment_date (YYYY-MM-DD, nullable)
- items: list of {sku, description, quantity, unit_net_price, vat_percentage,
  discount_percentage, source_total}
- total_net, vat_total, total

All monetary and quantity values must be plain decimal numbers (no currency symbols,
no thousands separators). Respond with JSON only, no commentary.
"""


def _encode_image(image_path: Path) -> tuple[str, str]:
    mime_type, _ = mimetypes.guess_type(image_path)
    if mime_type is None:
        mime_type = "image/png"
    data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return mime_type, data


def extract_order(image_path: str | Path, client: OpenAI | None = None) -> OrderData:
    """Extract structured OrderData from a single order image via a vision LLM.

    The model only interprets what's on the page; all downstream decisions
    (matching, creation, verification) are made by deterministic code.
    """
    image_path = Path(image_path)
    if not image_path.exists():
        raise ExtractionError(f"Image not found: {image_path}")

    client = client or OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    mime_type, encoded = _encode_image(image_path)

    log.info(f"Starting order extraction from {image_path.name}")

    # Uses the Responses API (OpenAI's current recommended API, superseding
    # Chat Completions) rather than client.chat.completions.create(). Shape
    # differences: instructions carries what used to be the system message;
    # input takes input_image/input_text parts (a flat image_url string, not
    # {"url": ...}); response_format is now nested under text.format; and
    # the output is read via the output_text convenience property rather
    # than choices[0].message.content.
    #
    # text.format type "json_object" requires the word "json" to appear in
    # the *input* messages specifically -- confirmed live: instructions
    # alone (which does say "JSON") isn't enough, the API 400s with "Response
    # input messages must contain the word 'json' in some form" until an
    # input_text part mentioning it is added alongside the image.
    try:
        response = client.responses.create(
            model=MODEL,
            instructions=EXTRACTION_PROMPT,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Extract this order as JSON, following the schema and rules above.",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime_type};base64,{encoded}",
                        },
                    ],
                },
            ],
            text={"format": {"type": "json_object"}},
        )
    except Exception as exc:
        raise ExtractionError(f"Vision API call failed: {exc}") from exc

    raw_content = response.output_text
    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"Model did not return valid JSON: {exc}") from exc

    try:
        order = OrderData.model_validate(payload)
    except PydanticValidationError as exc:
        raise ExtractionError(f"Extracted data failed schema validation: {exc}") from exc

    log.info(f"Extracted order {order.external_reference}")
    log.info(f"Extracted {len(order.items)} items")
    return order
