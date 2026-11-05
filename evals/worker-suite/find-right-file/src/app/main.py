from services.profile_loader import load_profile


def main() -> None:
    print(load_profile("default"))


if __name__ == "__main__":
    main()
