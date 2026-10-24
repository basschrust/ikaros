from pathlib import Path
import yaml

DEFAULT_CONFIG = "instruments.yaml"


class IncludeLoader(yaml.SafeLoader):
    pass


def include_constructor(loader, node):
    relative_path = loader.construct_scalar(node)

    base_path = Path(loader.name).parent
    file_path = base_path / relative_path

    with open(file_path, "r") as f:
        sub_loader = IncludeLoader(f)
        sub_loader.name = str(file_path)
        return sub_loader.get_single_data()


IncludeLoader.add_constructor("!include", include_constructor)


def load_config(filename: str = DEFAULT_CONFIG) -> dict:
    src_file = Path("config") / filename

    with open(src_file, "r") as f:
        loader = IncludeLoader(f)
        loader.name = src_file
        config = loader.get_single_data()

    return config
