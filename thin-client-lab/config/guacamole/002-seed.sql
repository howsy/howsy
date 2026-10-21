-- Portal configuration (step 8): one balancing group over sh-01 and sh-02,
-- granted to the AD group CC-Agents-Test (resolved through the LDAP extension).

-- The built-in guacadmin/guacadmin account must never be usable on a public portal.
UPDATE guacamole_user SET disabled = true
 WHERE entity_id = (SELECT entity_id FROM guacamole_entity WHERE name = 'guacadmin' AND type = 'USER');

INSERT INTO guacamole_entity (name, type) VALUES ('CC-Agents-Test', 'USER_GROUP');
INSERT INTO guacamole_user_group (entity_id)
  SELECT entity_id FROM guacamole_entity WHERE name = 'CC-Agents-Test' AND type = 'USER_GROUP';

INSERT INTO guacamole_connection_group (connection_group_name, type, enable_session_affinity)
  VALUES ('CC Desktop', 'BALANCING', true);

INSERT INTO guacamole_connection (connection_name, protocol, parent_id)
  SELECT h, 'rdp', connection_group_id
    FROM guacamole_connection_group, (VALUES ('sh-01'), ('sh-02')) AS hosts(h)
   WHERE connection_group_name = 'CC Desktop';

-- Session lockdown: no clipboard, drives, printing, SFTP or file transfer; every session recorded.
INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)
  SELECT c.connection_id, p.name, CASE WHEN p.name = 'hostname' THEN c.connection_name ELSE p.value END
    FROM guacamole_connection c,
         (VALUES ('hostname', NULL),
                 ('port', '3389'),
                 ('username', '${GUAC_USERNAME}'),
                 ('password', '${GUAC_PASSWORD}'),
                 ('security', 'any'),
                 ('ignore-cert', 'true'),
                 ('disable-copy', 'true'),
                 ('disable-paste', 'true'),
                 ('enable-drive', 'false'),
                 ('disable-download', 'true'),
                 ('disable-upload', 'true'),
                 ('enable-printing', 'false'),
                 ('enable-sftp', 'false'),
                 ('recording-path', '/var/lib/guacamole/recordings'),
                 ('create-recording-path', 'true'),
                 ('recording-name', '${GUAC_USERNAME}-${GUAC_DATE}-${GUAC_TIME}')) AS p(name, value);

INSERT INTO guacamole_connection_group_permission (entity_id, connection_group_id, permission)
  SELECT e.entity_id, g.connection_group_id, 'READ'
    FROM guacamole_entity e, guacamole_connection_group g
   WHERE e.name = 'CC-Agents-Test' AND e.type = 'USER_GROUP' AND g.connection_group_name = 'CC Desktop';

INSERT INTO guacamole_connection_permission (entity_id, connection_id, permission)
  SELECT e.entity_id, c.connection_id, 'READ'
    FROM guacamole_entity e, guacamole_connection c
   WHERE e.name = 'CC-Agents-Test' AND e.type = 'USER_GROUP';
