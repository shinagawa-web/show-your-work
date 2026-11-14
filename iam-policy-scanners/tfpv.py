import sys

import botocore.client
import botocore.exceptions

from iam_check.iam_check import main

original = botocore.client.BaseClient._make_api_call


def logged(self, operation_name, api_params):
    if self.meta.service_model.service_name != "accessanalyzer":
        return original(self, operation_name, api_params)
    try:
        response = original(self, operation_name, api_params)
    except botocore.exceptions.ClientError as error:
        print(f"api-call {operation_name} error={error.response['Error']['Code']}", file=sys.stderr, flush=True)
        raise
    print(f"api-call {operation_name} result={response.get('result', '-')}", file=sys.stderr, flush=True)
    return response


botocore.client.BaseClient._make_api_call = logged
sys.argv[0] = "tf-policy-validator"
sys.exit(main())
