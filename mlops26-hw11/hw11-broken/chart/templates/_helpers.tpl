{{- define "hw11.configName" -}}
{{- $identity := printf "%s:%s:%s" .Values.model (toJson .Values.backend) (.Files.Get "files/params.yaml") -}}
{{- printf "%s-config-%s" .Release.Name (sha256sum $identity | trunc 12) -}}
{{- end -}}
