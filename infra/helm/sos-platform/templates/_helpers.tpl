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
