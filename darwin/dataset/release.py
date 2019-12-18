import datetime
import shutil

import requests

from darwin.dataset.identifier import DatasetIdentifier


class Release:
    def __init__(
        self,
        dataset_slug,
        team_slug,
        version,
        url,
        export_date,
        image_count,
        class_count,
        available,
        latest,
    ):
        self.dataset_slug = dataset_slug
        self.team_slug = team_slug
        self.version = version
        self.url = url
        self.export_date = export_date
        self.image_count = image_count
        self.class_count = class_count
        self.available = available
        self.latest = latest

    @classmethod
    def parse_json(cls, dataset_slug, team_slug, payload):
        export_date = datetime.datetime.strptime(
            payload["inserted_at"], "%Y-%m-%dT%H:%M:%S%z"
        )
        if payload["download_url"] is None:
            return cls(
                dataset_slug=dataset_slug,
                team_slug=team_slug,
                version=payload["version"],
                export_date=export_date,
                available=False,
                image_count=None,
                class_count=None,
                latest=False
            )

        return cls(
            dataset_slug=dataset_slug,
            team_slug=team_slug,
            version=payload["name"],
            image_count=payload["metadata"]["num_images"],
            class_count=len(payload["metadata"]["annotation_classes"]),
            export_date=export_date,
            url=payload["download_url"],
            available=True,
            latest=payload["latest"]
        )

    def download_zip(self, path):
        with requests.get(self.url, stream=True) as r:
            with open(str(path), "wb") as f:
                shutil.copyfileobj(r.raw, f)
        return path

    @property
    def identifier(self) -> DatasetIdentifier:
        return DatasetIdentifier(f"{self.team_slug}/{self.dataset_slug}:{self.version}")
