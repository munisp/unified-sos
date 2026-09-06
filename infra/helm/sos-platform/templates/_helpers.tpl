{{/*
Common template helpers for the sos-platform umbrella chart.
Not a manifest — Helm never renders files prefixed with "_".
*/}}
{{- define "sos-platform.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "sos-platform.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "sos-platform.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "sos-platform.realm" -}}
{{- if .Values.global.keycloakRealm -}}
{{- .Values.global.keycloakRealm -}}
{{- else -}}
{{- printf "sos-%s" .Values.global.tenantStateId -}}
{{- end -}}
{{- end -}}

{{- define "sos-platform.labels" -}}
app.kubernetes.io/part-of: sos-platform
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
sos.gov.ng/tenant-state: {{ .Values.global.tenantStateId }}
sos.gov.ng/tenancy-tier: {{ .Values.global.tenancyTier }}
{{- end -}}

{{/*
Comma-separated TigerBeetle replica addresses (headless Service DNS),
in replica-index order — identical ordering required on all replicas.
*/}}
{{- define "sos-platform.tigerbeetleAddresses" -}}
{{- $fullname := include "sos-platform.fullname" . -}}
{{- $ns := .Release.Namespace -}}
{{- $count := int .Values.tigerbeetle.replicaCount -}}
{{- $addrs := list -}}
{{- range $i, $e := until $count -}}
{{- $addrs = append $addrs (printf "%s-tigerbeetle-%d.%s-tigerbeetle-headless.%s.svc.cluster.local:3000" $fullname $i $fullname $ns) -}}
{{- end -}}
{{- join "," $addrs -}}
{{- end -}}
