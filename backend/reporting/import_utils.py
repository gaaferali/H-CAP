def normalize_import_header(value):
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return {
        "phone": "phone_number",
        "mobile": "phone_number",
        "mobile_number": "phone_number",
        "beneficiary_no": "beneficiary_number",
        "national_id_number": "national_id",
    }.get(key, key)


def normalize_import_rows(rows):
    return [{normalize_import_header(key): value for key, value in row.items()} for row in rows]
