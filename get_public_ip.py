"""Print the bot's public outbound IP for Delta Exchange API allowlisting."""

from urllib.request import urlopen


def main() -> None:
    with urlopen("https://api.ipify.org", timeout=10) as response:
        public_ip = response.read().decode("ascii").strip()

    if not public_ip:
        raise RuntimeError("The IP lookup service returned an empty response")

    print(public_ip)


if __name__ == "__main__":
    main()
