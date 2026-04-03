def normalize_project_name(value: str) -> str:
    value = value.strip()
    value = value.replace("_", "-")
    value = "-".join(part for part in value.split("-") if part)
    return value.lower()


def normalize_repo_name(value: str) -> str:
    value = value.strip()
    value = value.replace("_", "-")
    value = "-".join(part for part in value.split("-") if part)
    return value.lower()
