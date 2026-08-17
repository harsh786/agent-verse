"""Avro parser — schema extraction + record sampling from Avro binary files."""
from __future__ import annotations

import io
import logging

_log = logging.getLogger(__name__)


class AvroParser:
    """Parse Apache Avro files into schema description + sampled records.

    Output format:
      Schema: {schema_name}
      Fields: field1 (type1), field2 (type2), ...
      Record 0: field1=val1, field2=val2, ...
      ...
    """

    SAMPLE_RECORDS = 50

    def parse(self, content: bytes, *, filename: str = "") -> str:
        try:
            import fastavro  # type: ignore[import-not-found]
        except ImportError:
            _log.warning("fastavro not installed — cannot parse Avro. pip install fastavro")
            return ""

        try:
            reader = fastavro.reader(io.BytesIO(content))
            schema = reader.writer_schema
        except Exception as exc:
            _log.warning("failed to open Avro file '%s': %s", filename, exc)
            return ""

        # Schema summary
        schema_name = schema.get("name", filename or "unknown") if isinstance(schema, dict) else str(schema)
        fields = schema.get("fields", []) if isinstance(schema, dict) else []
        field_strs = [f"{f.get('name', '?')} ({f.get('type', '?')})" for f in fields]
        schema_line = f"Schema: {schema_name}\nFields: " + ", ".join(field_strs)

        # Sample records
        records: list[str] = []
        for i, record in enumerate(reader):
            if i >= self.SAMPLE_RECORDS:
                break
            parts = [f"{k}={v}" for k, v in record.items() if v is not None]
            records.append(f"Record {i}: " + ", ".join(parts))

        header = f"File: {filename}\n" if filename else ""
        return header + schema_line + "\n" + "\n".join(records)
