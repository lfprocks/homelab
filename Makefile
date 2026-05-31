PHONY: age-secret-to-cluster
age-secret-to-cluster:
	cat age.agekey | kubectl create secret generic sops-age --namespace=flux-system --from-file=age.agekey=/dev/stdin