import os
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Union

import requests

from darwin.config import Config
from darwin.dataset import RemoteDataset
from darwin.dataset.identifier import DatasetIdentifier
from darwin.exceptions import (
    InsufficientStorage,
    InvalidLogin,
    MissingConfig,
    NotFound,
    Unauthorized,
)
from darwin.utils import is_project_dir, urljoin
from darwin.validators import ErrorHandlerType, name_taken, validation_error


class Client:
    def __init__(self, config: Config, default_team: Optional[str] = None):
        self.config = config
        self.url = config.get("global/api_endpoint")
        self.base_url = config.get("global/base_url")
        self.default_team = default_team or config.get("global/default_team")

    def get(self, endpoint: str, team: str, raw: bool = False, debug: bool = False):
        """Get something from the server trough HTTP

        Parameters
        ----------
        endpoint : str
            Recipient of the HTTP operation
        team : str
            Team slug, used to fetch the correct headers for the request
        raw : bool
            Flag for returning raw response
        debug : bool
            Debugging flag. In this case failed requests get printed

        Returns
        -------
        dict
        Dictionary which contains the server response

        Raises
        ------
        NotFound
            Resource not found
        Unauthorized
            Action is not authorized
        """
        response = requests.get(urljoin(self.url, endpoint), headers=self._get_headers(team))

        if response.status_code == 401:
            raise Unauthorized()
        if response.status_code == 404:
            raise NotFound(urljoin(self.url, endpoint))
        if raw:
            return response
        else:
            return self._decode_response(response, debug)

    def put(self, endpoint: str, payload: Dict, team: str, debug: bool = False):
        """Put something on the server trough HTTP

        Parameters
        ----------
        endpoint : str
            Recipient of the HTTP operation
        payload : dict
            What you want to put on the server (typically json encoded)
        team : str
            Team slug, used to fetch the correct headers for the request
        debug : bool
            Debugging flag. In this case failed requests get printed

        Returns
        -------
        dict
        Dictionary which contains the server response

        Raises
        ------
        Unauthorized
            Action is not authorized
        InsufficientStorage
            Insufficient storage left
        """
        response = requests.put(
            urljoin(self.url, endpoint), json=payload, headers=self._get_headers(team)
        )

        if response.status_code == 401:
            raise Unauthorized()

        if response.status_code == 429:
            error_code = response.json()["errors"]["code"]
            if error_code == "INSUFFICIENT_REMAINING_STORAGE":
                raise InsufficientStorage()

        return self._decode_response(response, debug)

    def post(
        self,
        endpoint: str,
        payload: Dict,
        team: str,
        error_handlers: Optional[List[ErrorHandlerType]] = None,
        debug: bool = False,
    ):
        """Post something new on the server trough HTTP

        Parameters
        ----------
        endpoint : str
            Recipient of the HTTP operation
        payload : dict
            What you want to put on the server (typically json encoded)
        team : str
            Team slug, used to fetch the correct headers for the request
        error_handlers : Optional[List[ErrorHandlerType]]
            Optional list of error handlers
        debug : bool
            Debugging flag. In this case failed requests get printed

        Returns
        -------
        dict
        Dictionary which contains the server response
        """
        if payload is None:
            payload = {}
        if error_handlers is None:
            error_handlers = []
        response = requests.post(
            urljoin(self.url, endpoint), json=payload, headers=self._get_headers(team)
        )
        if response.status_code == 401:
            raise Unauthorized()

        if response.status_code != 200:
            for error_handler in error_handlers:
                error_handler(response.status_code, response.json())

            if debug:
                print(
                    f"Client get request response ({response.json()}) with unexpected status "
                    f"({response.status_code}). "
                    f"Client: ({self})"
                    f"Request: (endpoint={endpoint}, payload={payload})"
                )

        return self._decode_response(response, debug)

    def list_local_datasets(self, team: Optional[str] = None) -> Iterator[Path]:
        """Returns a list of all local folders who are detected as dataset.

        Returns
        -------
        list[Path]
        List of all local datasets
        """
        team_config = self.config.get_team(team or self.default_team)

        for project_path in Path(team_config["datasets_dir"]).glob("*"):
            if project_path.is_dir() and is_project_dir(project_path):
                yield Path(project_path)

    def list_remote_datasets(self, team: Optional[str] = None) -> Iterator[RemoteDataset]:
        """Returns a list of all available datasets with the team currently authenticated against

        Returns
        -------
        list[RemoteDataset]
        List of all remote datasets
        """
        for dataset in self.get("/datasets/", team=team):
            yield RemoteDataset(
                name=dataset["name"],
                slug=dataset["slug"],
                team=team or self.default_team,
                dataset_id=dataset["id"],
                image_count=dataset["num_images"],
                progress=dataset["progress"],
                client=self,
            )

    def get_remote_dataset(
        self, dataset_identifier: Union[str, DatasetIdentifier]
    ) -> RemoteDataset:
        """Get a remote dataset based on the parameter passed. You can only choose one of the
        possible parameters and calling this method with multiple ones will result in an
        error.

        Parameters
        ----------
        dataset_identifier : int
            ID of the dataset to return

        Returns
        -------
        RemoteDataset
            Initialized dataset
        """
        if isinstance(dataset_identifier, str):
            dataset_identifier = DatasetIdentifier.parse(dataset_identifier)
        if not dataset_identifier.team_slug:
            dataset_identifier.team_slug = self.default_team

        matching_datasets = [
            dataset
            for dataset in self.list_remote_datasets(team=dataset_identifier.team_slug)
            if dataset.slug == dataset_identifier.dataset_slug
        ]
        if not matching_datasets:
            raise NotFound(dataset_identifier)
        return matching_datasets[0]

    def create_dataset(self, name: str, team: Optional[str] = None) -> RemoteDataset:
        """Create a remote dataset

        Parameters
        ----------
        name : str
            Name of the dataset to create

        Returns
        -------
        RemoteDataset
        The created dataset
        """
        dataset = self.post(
            "/datasets", {"name": name}, team=team, error_handlers=[name_taken, validation_error]
        )
        return RemoteDataset(
            name=dataset["name"],
            team=team or self.default_team,
            slug=dataset["slug"],
            dataset_id=dataset["id"],
            image_count=dataset["num_images"],
            progress=dataset["progress"],
            client=self,
        )

    def get_datasets_dir(self, team: Optional[str] = None):
        """Gets the dataset directory of the specified team or the default one

        Parameters
        ----------
        team: str
            Team to get the directory from

        Returns
        -------
        str
            Path of the datasets for the selected team or the default one
        """
        return self.config.get_team(team or self.default_team)["datasets_dir"]

    def set_datasets_dir(self, datasets_dir: Path, team: Optional[str] = None):
        """ Sets the dataset directory of the specified team or the default one

        Parameters
        ----------
        datasets_dir: Path
            Path to set as dataset directory of the team
        team: str
            Team to change the directory to
        """
        self.config.put(f"teams/{team or self.default_team}/datasets_dir", datasets_dir)

    def _get_headers(self, team: Optional[str] = None):
        """Get the headers of the API calls to the backend.

        Parameters
        ----------

        Returns
        -------
        dict
        Contains the Content-Type and Authorization key
        """
        header = {"Content-Type": "application/json"}

        team_config = self.config.get_team(team or self.default_team)
        api_key = team_config.get("api_key")

        if api_key is not None:
            header["Authorization"] = f"ApiKey {api_key}"
        return header

    @classmethod
    def local(cls, team_slug: Optional[str] = None):
        """Factory method to use the default configuration file to init the client

        Returns
        -------
        Client
        The inited client
        """
        config_path = Path.home() / ".darwin" / "config.yaml"
        return Client.from_config(config_path, team_slug=team_slug)

    @classmethod
    def from_config(cls, config_path: Path, team_slug: Optional[str] = None):
        """Factory method to create a client from the configuration file passed as parameter

        Parameters
        ----------
        config_path : str
            Path to a configuration file to use to create the client

        Returns
        -------
        Client
        The inited client
        """
        if not config_path.exists():
            raise MissingConfig()
        config = Config(config_path)

        return cls(config=config, default_team=team_slug)

    @classmethod
    def from_api_key(cls, api_key: str, datasets_dir: Optional[Path] = None):
        """Factory method to create a client given an API key

        Parameters
        ----------
        api_key: str
            API key to use to authenticate the client
        datasets_dir : str
            String where the client should be initialized from (aka the root path)

        Returns
        -------
        Client
            The inited client
        """
        if datasets_dir is None:
            datasets_dir = Path.home() / ".darwin" / "datasets"
        headers = {"Content-Type": "application/json", "Authorization": f"ApiKey {api_key}"}
        api_url = Client.default_api_url()
        response = requests.get(urljoin(api_url, "/users/token_info"), headers=headers)

        if response.status_code != 200:
            raise InvalidLogin()
        data = response.json()
        team_id = data["selected_team"]["id"]
        team = [team["slug"] for team in data["teams"] if team["id"] == team_id][0]

        config = Config(path=None)
        config.set_team(team=team, api_key=api_key, datasets_dir=str(datasets_dir))
        config.set_global(api_endpoint=api_url, base_url=Client.default_base_url())

        return cls(config=config, default_team=team)

    @staticmethod
    def default_api_url():
        """Returns the default api url"""
        return f"{Client.default_base_url()}/api/"

    @staticmethod
    def default_base_url():
        """Returns the default base url"""
        return os.getenv("DARWIN_BASE_URL", "https://darwin.v7labs.com")

    @staticmethod
    def _decode_response(response, debug: bool = False):
        """ Decode the response as JSON entry or return a dictionary with the error

        Parameters
        ----------
        response: requests.Response
            Response to decode
        debug : bool
            Debugging flag. In this case failed requests get printed

        Returns
        -------
        dict
        JSON decoded entry or error
        """
        try:
            return response.json()
        except ValueError:
            if debug:
                print(f"[ERROR {response.status_code}] {response.text}")
            response.close()
            return {
                "error": "Response is not JSON encoded",
                "status_code": response.status_code,
                "text": response.text,
            }

    def __str__(self):
        return f"Client(default_team={self.default_team})"
