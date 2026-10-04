"""Small fixture workspace for enterprise live-graph smoke tests."""


def normalize_name(name: str) -> str:
    return name.strip().title()


def build_message(name: str) -> str:
    clean_name = normalize_name(name)
    return f"Hello, {clean_name}"


def main() -> str:
    return build_message(" enterprise fixture ")


if __name__ == "__main__":
    print(main())
