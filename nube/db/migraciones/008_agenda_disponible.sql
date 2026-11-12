-- Agenda en línea con horarios libres: la página del cliente solo ofrece los espacios que no están ocupados.
-- Esta función devuelve, por token, las horas ocupadas en la hora local del negocio (las citas confirmadas y las
-- solicitudes por confirmar que ya pidieron una hora) y la hora actual. Sin nombres, teléfonos ni nada más: el cliente
-- nunca ve las citas de otros, solo que ese espacio ya no está libre. La API calcula los espacios con el horario de
-- atención del negocio (config.cita_publica).
-- p_excluir_tel: el teléfono que está pidiendo; su propia solicitud por confirmar no le ocupa el espacio que vuelve a pedir.
CREATE FUNCTION agenda_ocupada_publica(p_token text, p_zona_horaria text, p_excluir_tel text DEFAULT '')
  RETURNS TABLE (tipo text, cuando text)
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public
  AS $$
  DECLARE
    v_negocio uuid;
    v_tz      text := 'America/Mexico_City';
  BEGIN
    SELECT id INTO v_negocio FROM negocios WHERE token_publico = p_token AND activo;
    IF v_negocio IS NULL THEN
      RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = p_zona_horaria) THEN
      v_tz := p_zona_horaria;
    END IF;
    RETURN QUERY SELECT 'ahora'::text, to_char(now() AT TIME ZONE v_tz, 'YYYY-MM-DD"T"HH24:MI');
    RETURN QUERY
      SELECT 'confirmada'::text, to_char(s.confirmada_para AT TIME ZONE v_tz, 'YYYY-MM-DD"T"HH24:MI')
      FROM solicitudes_cita s
      WHERE s.negocio_id = v_negocio AND s.estado = 'confirmada'
        AND s.confirmada_para BETWEEN now() - interval '1 day' AND now() + interval '70 days';
    RETURN QUERY
      SELECT 'pendiente'::text, (s.datos->>'fecha') || 'T' || (s.datos->>'horario')
      FROM solicitudes_cita s
      WHERE s.negocio_id = v_negocio AND s.estado = 'pendiente' AND s.tel <> COALESCE(p_excluir_tel, '')
        AND s.datos->>'fecha' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND s.datos->>'horario' ~ '^[0-9]{2}:[0-9]{2}$';
  END
  $$;
REVOKE ALL ON FUNCTION agenda_ocupada_publica(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION agenda_ocupada_publica(text, text, text) TO app_api;
