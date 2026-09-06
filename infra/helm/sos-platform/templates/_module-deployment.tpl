{{/*
Generic module Deployment + Service for every SOS module service
(services/mod-*, control-plane, lakehouse). Renders the same labels,
probes and global tenant env wiring as the original mod-rev-core
templates; module-specific env vars come from .Values.modules.<key>.env
as raw ENV_NAME: value pairs.

Invoked from modules.yaml as:
  include "sos-platform.moduleDeployment"
    (dict "root" $ "name" <kebab-name> "module" <module values>)
*/}}
{{- define "sos-platform.moduleName" -}}
{{- regexReplaceAll "([a-z0-9])([A-Z])" . "${1}-${2}" | lower -}}
{{- end -}}

{{- define "sos-platform.moduleDeployment" -}}
{{- $root := .root -}}
{{- $name := .name -}}
{{- $mod := .module -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: "{{ include "sos-platform.fullname" $root }}-{{ $name }}"
  labels:
    app.kubernetes.io/component: {{ $name }}
{{ include "sos-platform.labels" $root | indent 4 }}
spec:
  replicas: {{ $mod.replicaCount }}
  selector:
    matchLabels:
      app.kubernetes.io/component: {{ $name }}
      app.kubernetes.io/instance: {{ $root.Release.Name }}
  template:
    metadata:
      labels:
        app.kubernetes.io/component: {{ $name }}
        app.kubernetes.io/instance: {{ $root.Release.Name }}
        sos.gov.ng/tenant-state: {{ $root.Values.global.tenantStateId }}
    spec:
      containers:
        - name: {{ $name }}
          image: "{{ $mod.image.repository }}:{{ $mod.image.tag }}"
          ports:
            - name: http
              containerPort: {{ $mod.service.port }}
          env:
            - name: SOS_TENANT_STATE
              value: {{ $root.Values.global.tenantStateId | quote }}
            - name: SOS_POLICY_PACK_PATH
              value: {{ $root.Values.global.policyPackPath | quote }}
            - name: SOS_POSTGRES_HOST
              value: {{ $root.Values.global.postgresHost | quote }}
            {{- range $envName, $envValue := $mod.env }}
            - name: {{ $envName }}
              value: {{ $envValue | quote }}
            {{- end }}
          {{- if $mod.secretEnvFrom }}
          # Live-mode credentials are mounted whole-secret from the
          # ExternalSecrets/SealedSecrets sets under infra/secrets/.
          # Adapters fail closed at boot if a required key is absent.
          envFrom:
            {{- range $mod.secretEnvFrom }}
            - secretRef:
                name: {{ . | quote }}
            {{- end }}
          {{- end }}
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            initialDelaySeconds: 15
            periodSeconds: 20
          resources:
{{ toYaml $mod.resources | indent 12 }}
---
apiVersion: v1
kind: Service
metadata:
  name: "{{ include "sos-platform.fullname" $root }}-{{ $name }}"
  labels:
    app.kubernetes.io/component: {{ $name }}
{{ include "sos-platform.labels" $root | indent 4 }}
spec:
  type: ClusterIP
  ports:
    - name: http
      port: {{ $mod.service.port }}
      targetPort: http
  selector:
    app.kubernetes.io/component: {{ $name }}
    app.kubernetes.io/instance: {{ $root.Release.Name }}
{{ end }}
